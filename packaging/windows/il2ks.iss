; il2ks Windows installer (doc 07, option B). Built by packaging/windows/build.py, which passes the /D defines below.
;
; Layout of an install:
;   %ProgramFiles%\il2ks\            program files only, replaced at every upgrade: python\ (relocatable CPython with il2ks and its
;                                    dependencies), bin\caddy.exe, service\il2ks-service.exe (+ .xml), il2ks.cmd, licenses\
;   %ProgramData%\il2ks\             everything that is yours: il2ks.toml, database, archived logs, backups, custom\, logs\
;                                    (kept on uninstall unless you say otherwise)
;
; Flow: wizard (welcome, folder, tasks, Install) copies the files. Then the configuration pages (game log folder, time zone,
; domain, HTTPS mode, admin account) run `il2ks setup --non-interactive`, register the service, open the firewall if asked
; and start it. An upgrade (il2ks.toml exists) skips the pages: stop the service, `il2ks backup`, replace the program
; files, register the service again, start it. Database migrations run when the service starts (after their own backup).
;
; Silent installs (CI, scripted rollouts) take the answers from switches, see the "Command line" part of [Code]:
;   /LOGDIR= /TIMEZONE= /DOMAIN= /EMAIL= /HTTPS=caddy|external /ADMINUSER= /ADMINPASSWORD=
;   /NOSETUP (copy files only)   /NOSERVICE (no service, no start)   /NOFIREWALL   /MERGETASKS="!firewall"   /DELETEDATA (uninstall)

#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppVersionNumeric
  #define AppVersionNumeric "0.0.0.0"
#endif
#ifndef StageDir
  #error StageDir is not defined: build with packaging/windows/build.py
#endif
#ifndef OutputDir
  #define OutputDir "..\..\dist"
#endif

[Setup]
; Never change AppId: it is how an upgrade finds the existing install.
AppId={{6F1D2C3A-8B7E-4D5F-9A10-2E4B6C8D0F13}
AppName=il2ks
AppVersion={#AppVersion}
AppVerName=il2ks {#AppVersion}
AppPublisher=il2ks, IL-2 Korea stats
AppPublisherURL=https://github.com/FGlazov/il2_korea_stats
AppSupportURL=https://github.com/FGlazov/il2_korea_stats/issues
AppUpdatesURL=https://github.com/FGlazov/il2_korea_stats/releases
VersionInfoVersion={#AppVersionNumeric}
VersionInfoDescription=il2ks stats site installer
DefaultDirName={autopf}\il2ks
DisableProgramGroupPage=yes
DisableReadyPage=no
PrivilegesRequired=admin
MinVersion=10.0
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#OutputDir}
OutputBaseFilename=il2ks-setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
UninstallDisplayName=il2ks stats site
SetupLogging=yes
ShowLanguageDialog=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "firewall"; Description: "Allow visitors through Windows Firewall (TCP ports 80 and 443, only for the bundled Caddy web proxy)"; GroupDescription: "Network:"

[Dirs]
Name: "{commonappdata}\il2ks"; Flags: uninsneveruninstall
Name: "{commonappdata}\il2ks\logs"; Flags: uninsneveruninstall

[Files]
Source: "{#StageDir}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; An upgrade must not leave the old Python, packages or Caddy behind: two versions of a package side by side break imports.
Type: filesandordirs; Name: "{app}\python"
Type: filesandordirs; Name: "{app}\bin"

[Icons]
Name: "{autoprograms}\il2ks\View logs"; Filename: "{commonappdata}\il2ks\logs"
Name: "{autoprograms}\il2ks\Run doctor (check the setup)"; Filename: "{app}\il2ks-admin.cmd"; Parameters: "doctor"; WorkingDir: "{app}"; Comment: "Checks the configuration, ports, Caddy and the log folder"
Name: "{autoprograms}\il2ks\il2ks command prompt"; Filename: "{app}\il2ks-admin.cmd"; Parameters: "shell"; WorkingDir: "{app}"; Comment: "A command prompt where the il2ks command works"
Name: "{autoprograms}\il2ks\Uninstall il2ks"; Filename: "{uninstallexe}"

[UninstallDelete]
Type: files; Name: "{autoprograms}\il2ks\*.url"
Type: dirifempty; Name: "{autoprograms}\il2ks"

[Run]
Filename: "{autoprograms}\il2ks\Open stats site.url"; Description: "Open the stats site now"; Flags: postinstall shellexec skipifsilent nowait unchecked; Check: SiteShortcutExists

[Code]
const
  ServiceId = 'il2ks';
  FirewallHttp = 'il2ks HTTP (80)';
  FirewallHttps = 'il2ks HTTPS (443)';
  // Runs with the installed Python: the folders that hold missionReport files, newest first, one per line (il2ks.ops.detect).
  DetectCode = 'import os,sys,pathlib;from il2ks.ops.setup import default_log_finder as d;[print(f.path) for f in d(os.environ,pathlib.Path.home(),sys.platform)()]';

var
  LogPage: TInputDirWizardPage;
  SitePage: TInputQueryWizardPage;
  HttpsPage: TInputOptionWizardPage;
  AdminPage: TInputQueryWizardPage;
  Progress: TOutputProgressWizardPage;
  HaveConfig: Boolean;
  Finished: Boolean;

function SetEnvironmentVariableW(Name, Value: String): Boolean;
  external 'SetEnvironmentVariableW@kernel32.dll stdcall';

// function WindowsToIana(const WindowsName: String): String, generated from zones.py by build.py (ISCC /I<work folder>)
#include "windows_zones.inc"

// --- small helpers ----------------------------------------------------------------------------------------------------

function DataDir: String;
begin
  Result := ExpandConstant('{commonappdata}\il2ks');
end;

function ConfigPath: String;
begin
  Result := DataDir + '\il2ks.toml';
end;

function PythonExe: String;
begin
  Result := ExpandConstant('{app}\python\python.exe');
end;

function ServiceExe: String;
begin
  Result := ExpandConstant('{app}\service\il2ks-service.exe');
end;

function SwitchGiven(const Name: String): Boolean;
var
  I: Integer;
begin
  Result := False;
  for I := 1 to ParamCount do
    if CompareText(ParamStr(I), '/' + Name) = 0 then
      Result := True;
end;

function Param(const Name: String): String;
begin
  Result := ExpandConstant('{param:' + Name + '|}');
end;

// Runs a program hidden, waits, and returns what it printed. Exit code in ExitCode (-1 if it could not start).
function RunCaptured(const FileName, Params: String; var Output: String): Integer;
var
  Tmp: String;
  Raw: AnsiString;
  Code: Integer;
begin
  Tmp := ExpandConstant('{tmp}\il2ks-output.txt');
  DeleteFile(Tmp);
  if Exec(ExpandConstant('{cmd}'), '/S /C ""' + FileName + '" ' + Params + ' > "' + Tmp + '" 2>&1"', '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Result := Code
  else
    Result := -1;
  Output := '';
  if LoadStringFromFile(Tmp, Raw) then
    Output := String(Raw);
end;

function RunQuiet(const FileName, Params: String): Integer;
var
  Ignored: String;
begin
  Result := RunCaptured(FileName, Params, Ignored);
end;

function LastLines(const Text: String; Count: Integer): String;
var
  I, Seen: Integer;
begin
  Result := Trim(Text);
  Seen := 0;
  for I := Length(Result) downto 1 do
  begin
    if Result[I] = #10 then
      Seen := Seen + 1;
    if Seen = Count then
    begin
      Result := Copy(Result, I + 1, Length(Result));
      Exit;
    end;
  end;
end;

procedure Say(const Text: String);
begin
  Log(Text);
  if (Progress <> nil) and not WizardSilent then
    Progress.SetText(Text, '');
end;

procedure Warn(const Text: String);
begin
  Log('WARNING: ' + Text);
  if not WizardSilent then
    MsgBox(Text, mbError, MB_OK);
end;

function SiteShortcutExists: Boolean;
begin
  Result := FileExists(ExpandConstant('{autoprograms}\il2ks\Open stats site.url'));
end;

function ServiceExists: Boolean;
var
  Output: String;
begin
  Result := RunCaptured(ExpandConstant('{sys}\sc.exe'), 'query ' + ServiceId, Output) = 0;
end;

function ServiceStopped: Boolean;
var
  Output: String;
begin
  RunCaptured(ExpandConstant('{sys}\sc.exe'), 'query ' + ServiceId, Output);
  Result := (Pos('STOPPED', Output) > 0) or (Pos('1060', Output) > 0);
end;

procedure StopService;
var
  I: Integer;
begin
  if not ServiceExists then
    Exit;
  Log('Stopping the il2ks service');
  if FileExists(ServiceExe) then
    RunQuiet(ServiceExe, 'stop')
  else
    RunQuiet(ExpandConstant('{sys}\sc.exe'), 'stop ' + ServiceId);
  for I := 1 to 60 do
  begin
    if ServiceStopped then
      Exit;
    Sleep(1000);
  end;
  Log('The service did not report STOPPED within 60 seconds');
end;

procedure RemoveService;
begin
  if ServiceExists then
  begin
    StopService;
    if FileExists(ServiceExe) then
      RunQuiet(ServiceExe, 'uninstall')
    else
      RunQuiet(ExpandConstant('{sys}\sc.exe'), 'delete ' + ServiceId);
  end;
end;

procedure RemoveFirewallRules;
var
  Netsh: String;
begin
  Netsh := ExpandConstant('{sys}\netsh.exe');
  RunQuiet(Netsh, 'advfirewall firewall delete rule name="' + FirewallHttp + '"');
  RunQuiet(Netsh, 'advfirewall firewall delete rule name="' + FirewallHttps + '"');
end;

procedure AddFirewallRules;
var
  Netsh, Caddy: String;
begin
  Netsh := ExpandConstant('{sys}\netsh.exe');
  Caddy := ExpandConstant('{app}\bin\caddy.exe');
  RemoveFirewallRules;
  RunQuiet(Netsh, 'advfirewall firewall add rule name="' + FirewallHttp + '" dir=in action=allow protocol=TCP localport=80 program="' + Caddy + '" enable=yes');
  RunQuiet(Netsh, 'advfirewall firewall add rule name="' + FirewallHttps + '" dir=in action=allow protocol=TCP localport=443 program="' + Caddy + '" enable=yes');
end;

function ConfigUsesOwnProxy: Boolean;
var
  Text: AnsiString;
begin
  Result := False;
  if LoadStringFromFile(ConfigPath, Text) then
    Result := Pos('mode = "external"', String(Text)) > 0;
end;

procedure WriteUrlShortcut(const Name, Url: String);
begin
  SaveStringToFile(ExpandConstant('{autoprograms}\il2ks\') + Name + '.url', '[InternetShortcut]' + #13#10 + 'URL=' + Url + #13#10, False);
end;

// --- wizard pages -----------------------------------------------------------------------------------------------------

procedure AddNote(Page: TWizardPage; Top, Height: Integer; const Text: String);
var
  Note: TNewStaticText;
begin
  Note := TNewStaticText.Create(Page);
  Note.Parent := Page.Surface;
  Note.Left := 0;
  Note.Top := Top;
  Note.Width := Page.SurfaceWidth;
  Note.Height := Height;
  Note.AutoSize := False;
  Note.WordWrap := True;
  Note.Caption := Text;
end;

// The machine's Windows time zone as an IANA name; '' if it is not in the table (the admin types it then).
function DefaultTimeZone: String;
var
  WindowsName: String;
begin
  Result := '';
  if RegQueryStringValue(HKLM, 'SYSTEM\CurrentControlSet\Control\TimeZoneInformation', 'TimeZoneKeyName', WindowsName) then
    Result := WindowsToIana(Trim(WindowsName));
end;

procedure InitializeWizard;
var
  Last: TNewEdit;
begin
  HaveConfig := FileExists(ConfigPath);

  // The pages come after "Installing": the folder search and the setup run with the Python that was just installed.
  LogPage := CreateInputDirPage(wpInstalling, 'Game server logs',
    'Where does your IL-2 Korea server write its text mission logs?',
    'il2ks reads the missionReport(...)[N].txt files from this folder. The installer looked for it and filled in the best guess. ' +
    'Leave it empty if you do not know yet: you can set [logs] dir in il2ks.toml later.', False, '');
  LogPage.Add('Folder with the missionReport files:');

  SitePage := CreateInputQueryPage(LogPage.ID, 'Time zone and web address',
    'When was each mission played, and where will visitors find the site?', '');
  SitePage.Add('Time zone of the game server''s computer (IANA name, for example Europe/Berlin, Asia/Seoul, America/New_York, UTC):', False);
  SitePage.Add('Domain name of the site (optional), for example stats.example.com:', False);
  SitePage.Add('E-mail for certificate notices (optional):', False);
  SitePage.Values[0] := DefaultTimeZone;
  Last := TNewEdit(SitePage.Edits[2]);
  AddNote(SitePage, Last.Top + Last.Height + ScaleY(10), ScaleY(110),
    'DServer names its log files in the computer''s local time, so il2ks has to know the time zone.' + #13#10 + #13#10 +
    'The domain name must point at this computer. It gives you a normal, trusted HTTPS certificate, fetched and renewed by itself. ' +
    'Without a domain the site still works, with a self-signed test certificate: browsers show a "not secure" warning. ' +
    'You can add the domain later in il2ks.toml. The e-mail is only used by the certificate authority to warn you if renewal ever fails.');

  HttpsPage := CreateInputOptionPage(SitePage.ID, 'HTTPS', 'How should the site get its HTTPS certificate?', '', True, False);
  HttpsPage.Add('il2ks does it for me with the bundled Caddy (recommended). Needs ports 80 and 443 to be free.');
  HttpsPage.Add('I already run my own web server (IIS, nginx, ...) in front of il2ks. il2ks then only serves 127.0.0.1:8000.');
  HttpsPage.Values[0] := True;

  AdminPage := CreateInputQueryPage(HttpsPage.ID, 'Admin account',
    'Create the account you use to manage the site.', 'It opens the admin pages: branding, hiding players, ingestion history.');
  AdminPage.Add('User name:', False);
  AdminPage.Add('Password (at least 8 characters, not a common password):', True);
  AdminPage.Add('Password again:', True);
  AdminPage.Values[0] := 'admin';
end;

function ShouldSkipPage(PageID: Integer): Boolean;
begin
  Result := HaveConfig and ((PageID = LogPage.ID) or (PageID = SitePage.ID) or (PageID = HttpsPage.ID) or (PageID = AdminPage.ID));
end;

// --- the work after the files are copied ------------------------------------------------------------------------------

procedure DetectLogFolder;
var
  Output: String;
begin
  if Param('LOGDIR') <> '' then
  begin
    LogPage.Values[0] := Param('LOGDIR');
    Exit;
  end;
  if WizardSilent or not FileExists(PythonExe) then
    Exit;
  WizardForm.StatusLabel.Caption := 'Looking for your game server logs...';
  if RunCaptured(PythonExe, '-c "' + DetectCode + '"', Output) = 0 then
  begin
    Output := Trim(Output);
    if Output <> '' then
    begin
      // first line = the folder with the newest report
      if Pos(#10, Output) > 0 then
        LogPage.Values[0] := Trim(Copy(Output, 1, Pos(#10, Output) - 1))
      else
        LogPage.Values[0] := Output;
    end;
  end;
end;

procedure RestrictDataDir;
var
  Icacls: String;
begin
  // Only SYSTEM (the service) and administrators may read the database, secret key and configuration.
  Icacls := ExpandConstant('{sys}\icacls.exe');
  RunQuiet(Icacls, '"' + DataDir + '" /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F');
end;

function RunSetup(const Domain, Email, Mode, LogDir, TimeZone, AdminUser, AdminPassword: String): Boolean;
var
  Args, Output: String;
  Code: Integer;
begin
  Args := '-m il2ks --config "' + ConfigPath + '" setup --non-interactive --data-dir "' + DataDir + '" --https ' + Mode;
  if LogDir <> '' then
    Args := Args + ' --logs-dir "' + LogDir + '"';
  if TimeZone <> '' then
    Args := Args + ' --timezone "' + TimeZone + '"';
  if Domain <> '' then
    Args := Args + ' --domain "' + Domain + '"';
  if Email <> '' then
    Args := Args + ' --email "' + Email + '"';
  if AdminPassword <> '' then
    Args := Args + ' --admin-username "' + AdminUser + '"'
  else
    Args := Args + ' --no-admin';
  SetEnvironmentVariableW('IL2KS_ADMIN_PASSWORD', AdminPassword);  // not on the command line: nobody can see it in a process list
  Code := RunCaptured(PythonExe, Args, Output);
  SetEnvironmentVariableW('IL2KS_ADMIN_PASSWORD', '');
  Log('il2ks setup exit code ' + IntToStr(Code) + #13#10 + Output);
  Result := (Code = 0) and FileExists(ConfigPath);
  if Code = 1 then
  begin
    // exit code 1 = configured, but the admin account was refused (password too weak?)
    Result := FileExists(ConfigPath);
    Warn('The site was set up, but the admin account was not created:' + #13#10 + #13#10 + LastLines(Output, 4) + #13#10 + #13#10 +
         'Create it later in the il2ks command prompt (Start menu) with: il2ks createadmin');
  end
  else if not Result then
    Warn('il2ks setup did not finish (exit code ' + IntToStr(Code) + '):' + #13#10 + #13#10 + LastLines(Output, 6) + #13#10 + #13#10 +
         'Nothing is running yet. Fix the problem, then run: il2ks setup   in the il2ks command prompt (Start menu).');
end;

function InstallAndStartService: Boolean;
var
  Code: Integer;
  Output: String;
begin
  RemoveService;
  Code := RunCaptured(ServiceExe, 'install', Output);
  Log('il2ks-service install: ' + IntToStr(Code) + #13#10 + Output);
  Result := Code = 0;
  if not Result then
  begin
    Warn('The Windows service could not be installed (exit code ' + IntToStr(Code) + '):' + #13#10 + LastLines(Output, 5));
    Exit;
  end;
  Code := RunCaptured(ServiceExe, 'start', Output);
  Log('il2ks-service start: ' + IntToStr(Code) + #13#10 + Output);
  Result := Code = 0;
  if not Result then
    Warn('The service was installed but did not start (exit code ' + IntToStr(Code) + '). ' +
         'Run "Run doctor" from the Start menu, and read the newest files in ' + DataDir + '\logs.');
end;

// Everything after the files are in place. Interactive fresh installs call it from the last page, upgrades and silent runs
// from ssPostInstall.
procedure FinishInstall;
var
  Mode, Domain: String;
  SetupOk, Service: Boolean;
begin
  if Finished then
    Exit;
  Finished := True;
  if not WizardSilent then
  begin
    Progress := CreateOutputProgressPage('Setting up il2ks', 'Please wait a moment.');
    Progress.Show;
  end;
  try
    Say('Protecting the data folder...');
    RestrictDataDir;

    SetupOk := True;
    Mode := 'caddy';
    if HaveConfig then
    begin
      if ConfigUsesOwnProxy then
        Mode := 'external';
    end
    else if not SwitchGiven('NOSETUP') then
    begin
      if WizardSilent then
      begin
        if Param('HTTPS') <> '' then
          Mode := Param('HTTPS');
        SetupOk := RunSetup(Param('DOMAIN'), Param('EMAIL'), Mode, Param('LOGDIR'), Param('TIMEZONE'), Param('ADMINUSER'), Param('ADMINPASSWORD'));
      end
      else
      begin
        if HttpsPage.Values[1] then
          Mode := 'external';
        Say('Creating the configuration, database and admin account...');
        SetupOk := RunSetup(Trim(SitePage.Values[1]), Trim(SitePage.Values[2]), Mode, Trim(LogPage.Values[0]), Trim(SitePage.Values[0]),
                            Trim(AdminPage.Values[0]), AdminPage.Values[1]);
      end;
      if SetupOk then
      begin
        Domain := Trim(SitePage.Values[1]);
        if WizardSilent then
          Domain := Param('DOMAIN');
        if Domain = '' then
          Domain := 'localhost';
        WriteUrlShortcut('Open stats site', 'https://' + Domain + '/');
        WriteUrlShortcut('Open admin', 'https://' + Domain + '/admin/');
      end;
    end
    else
      SetupOk := FileExists(ConfigPath);

    Service := (not SwitchGiven('NOSERVICE')) and SetupOk and FileExists(ConfigPath);
    if Service then
    begin
      Say('Installing and starting the il2ks service...');
      InstallAndStartService;
    end;
    if (not SwitchGiven('NOFIREWALL')) and (not SwitchGiven('NOSERVICE')) and WizardIsTaskSelected('firewall') and (Mode = 'caddy') then
    begin
      Say('Opening ports 80 and 443 in Windows Firewall...');
      AddFirewallRules;
    end;
  finally
    if Progress <> nil then
      Progress.Hide;
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    if not HaveConfig then
      DetectLogFolder;
    if HaveConfig or WizardSilent then
      FinishInstall;
  end;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
var
  Domain: String;
begin
  Result := True;
  if CurPageID = LogPage.ID then
  begin
    if (Trim(LogPage.Values[0]) <> '') and not DirExists(Trim(LogPage.Values[0])) then
      Result := MsgBox('The folder "' + Trim(LogPage.Values[0]) + '" does not exist. Use it anyway?', mbConfirmation, MB_YESNO) = IDYES;
  end
  else if CurPageID = SitePage.ID then
  begin
    Domain := Trim(SitePage.Values[1]);
    if Trim(SitePage.Values[0]) = '' then
    begin
      MsgBox('Please enter the time zone, for example Europe/Berlin or UTC.', mbError, MB_OK);
      Result := False;
    end
    else if (Pos(' ', Domain) > 0) or (Pos('/', Domain) > 0) or (Pos(':', Domain) > 0) then
    begin
      MsgBox('Please enter the domain name only, like stats.example.com: no https://, no port, no spaces.', mbError, MB_OK);
      Result := False;
    end;
  end
  else if CurPageID = AdminPage.ID then
  begin
    if Trim(AdminPage.Values[0]) = '' then
    begin
      MsgBox('Please enter a user name for the admin account.', mbError, MB_OK);
      Result := False;
    end
    else if Length(AdminPage.Values[1]) < 8 then
    begin
      MsgBox('The password needs at least 8 characters.', mbError, MB_OK);
      Result := False;
    end
    else if AdminPage.Values[1] <> AdminPage.Values[2] then
    begin
      MsgBox('The two passwords are not the same.', mbError, MB_OK);
      Result := False;
    end;
    if Result then
      FinishInstall;  // fresh interactive install: everything has been asked
  end;
end;

// --- upgrade: stop the service and back up before the files are replaced --------------------------------------------------

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Output: String;
  Code: Integer;
begin
  Result := '';
  if ServiceExists then
  begin
    WizardForm.StatusLabel.Caption := 'Stopping the il2ks service...';
    StopService;
  end;
  if FileExists(ConfigPath) and FileExists(PythonExe) then
  begin
    WizardForm.StatusLabel.Caption := 'Backing up your data...';
    Code := RunCaptured(PythonExe, '-m il2ks --config "' + ConfigPath + '" backup', Output);
    Log('il2ks backup before the upgrade: exit code ' + IntToStr(Code) + #13#10 + Output);
    if Code <> 0 then
    begin
      Result := 'The backup before the upgrade failed, so nothing was changed:' + #13#10 + LastLines(Output, 5);
      if not WizardSilent then
        if MsgBox(Result + #13#10 + #13#10 + 'Continue without a backup?', mbError, MB_YESNO or MB_DEFBUTTON2) = IDYES then
          Result := '';
    end;
  end;
end;

// --- uninstall ------------------------------------------------------------------------------------------------------------

procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  Delete: Boolean;
begin
  if CurUninstallStep = usUninstall then
  begin
    RemoveService;
    RemoveFirewallRules;
  end
  else if CurUninstallStep = usPostUninstall then
  begin
    Delete := SwitchGiven('DELETEDATA');
    if (not Delete) and (not UninstallSilent) and DirExists(DataDir) then
      Delete := MsgBox('Also delete your il2ks data (database, archived logs, backups, configuration) in ' + DataDir + '?' + #13#10 + #13#10 +
                       'Choose No to keep it: a later install picks it up again.', mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES;
    if Delete then
      DelTree(DataDir, True, True, True);
  end;
end;
