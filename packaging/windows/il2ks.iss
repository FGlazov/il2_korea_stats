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
;   /LOGDIR= /TIMEZONE= /DOMAIN= /EMAIL= /HTTPS=caddy|external /ADMINUSER= /ADMINPASSWORDFILE=<file with the password; deleted after use>
;   (/ADMINPASSWORD= still works but puts the password on the installer command line and in its log: prefer the file)
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
  ServiceAccount = 'NT SERVICE\il2ks';  // the service's virtual account (OQ-41)
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
  LogFolderChosen: String;  // the game log folder the service account is given rights on (GrantServiceAccount)

// Every program the installer starts with the installed Python gets -P first: the current folder (and the folder of a
// script) is then not put on sys.path, so a stray file named like a module cannot be imported by a service that runs
// with the rights of an account. Same flag in il2ks.cmd and the service definition (winsw.py).
const
  PythonSafeFlag = '-P ';

// One command-line argument for Windows' standard parser (the one Python uses): in double quotes, and the backslashes
// before the closing quote doubled, so 'C:\Games\Logs\' does not turn the closing quote into a literal one. Quotes inside
// the value cannot occur in a Windows path and are dropped. Mirrors packaging/windows/quoting.py, which a test checks.
function QuoteArg(const Value: String): String;
var
  Text: String;
  Trailing: Integer;
begin
  Text := Value;
  StringChangeEx(Text, '"', '', True);
  Trailing := 0;
  while (Length(Text) > Trailing) and (Text[Length(Text) - Trailing] = '\') do
    Trailing := Trailing + 1;
  Result := '"' + Text;
  while Trailing > 0 do
  begin
    Result := Result + '\';
    Trailing := Trailing - 1;
  end;
  Result := Result + '"';
end;

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
  // < NUL: a child that asks something on stdin gets end-of-file instead of waiting for a person who is not there (silent installs).
  Log('Running: ' + FileName + ' ' + Params);
  if Exec(ExpandConstant('{cmd}'), '/S /C ""' + FileName + '" ' + Params + ' > "' + Tmp + '" 2>&1 < NUL"', '', SW_HIDE, ewWaitUntilTerminated, Code) then
    Result := Code
  else
    Result := -1;
  Log('  exit code ' + IntToStr(Result));
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
  AddNote(SitePage, SitePage.Edits[2].Top + SitePage.Edits[2].Height + ScaleY(10), ScaleY(110),
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
  // Silent runs take every answer from the switches (FinishInstall): the pages are never shown, so their edits stay empty.
  Result := (WizardSilent or HaveConfig) and ((PageID = LogPage.ID) or (PageID = SitePage.ID) or (PageID = HttpsPage.ID) or (PageID = AdminPage.ID));
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
  if RunCaptured(PythonExe, PythonSafeFlag + '-c "' + DetectCode + '"', Output) = 0 then
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
  // Only SYSTEM and administrators may read the database, secret key and configuration. The service's own account gets
  // its rights in GrantServiceAccount, once the service exists (its name cannot be resolved before that).
  Icacls := ExpandConstant('{sys}\icacls.exe');
  RunQuiet(Icacls, QuoteArg(DataDir) + ' /inheritance:r /grant:r *S-1-5-18:(OI)(CI)F *S-1-5-32-544:(OI)(CI)F');
end;

// The service runs as its own virtual account NT SERVICE\il2ks (decision OQ-41): no password, no rights anywhere except
// where it is granted. It needs to change everything in the data folder (database, logs, certificates, backups). The
// program folder stays read-only for it (Program Files is readable by every user).
procedure GrantServiceAccount;
var
  Icacls, Output: String;
  Code: Integer;
begin
  Icacls := ExpandConstant('{sys}\icacls.exe');
  Code := RunCaptured(Icacls, QuoteArg(DataDir) + ' /grant "' + ServiceAccount + ':(OI)(CI)M" /T /C /Q', Output);
  Log('icacls grant ' + ServiceAccount + ': exit code ' + IntToStr(Code) + #13#10 + Output);
  // Best effort: files made by an older install (which ran as SYSTEM) or by this installer belong to administrators.
  // Owning them lets the service repair its own permissions later.
  Code := RunCaptured(Icacls, QuoteArg(DataDir) + ' /setowner "' + ServiceAccount + '" /T /C /Q', Output);
  Log('icacls setowner ' + ServiceAccount + ': exit code ' + IntToStr(Code) + #13#10 + Output);
  // The game's log folder: il2ks reads the reports and (after_archive = "move", the default) moves them away. A local
  // folder only; an upgrade does not ask again (the rights given at the first install stay).
  if (LogFolderChosen <> '') and DirExists(LogFolderChosen) then
  begin
    Code := RunCaptured(Icacls, QuoteArg(LogFolderChosen) + ' /grant "' + ServiceAccount + ':(OI)(CI)M" /C /Q', Output);
    Log('icacls grant on the log folder: exit code ' + IntToStr(Code) + #13#10 + Output);
  end;
end;

// The admin password reaches `il2ks setup` through a file, never through its command line or environment (both can be
// read by other programs, and Inno's own log records the installer's command line). The file is created empty, locked to
// administrators and SYSTEM, and only then filled; the caller deletes it. 'il2ks setup' reads it as UTF-8 (BOM allowed).
function WritePasswordFile(const Password: String): String;
var
  Lines: TArrayOfString;
begin
  Result := ExpandConstant('{tmp}\il2ks-admin-password.txt');
  DeleteFile(Result);
  SaveStringToFile(Result, '', False);
  RunQuiet(ExpandConstant('{sys}\icacls.exe'), QuoteArg(Result) + ' /inheritance:r /grant:r *S-1-5-18:F *S-1-5-32-544:F');
  SetArrayLength(Lines, 1);
  Lines[0] := Password;
  SaveStringsToUTF8File(Result, Lines, False);
end;

function RunSetup(const Domain, Email, Mode, LogDir, TimeZone, AdminUser, AdminPassword: String): Boolean;
var
  Args, Output, PasswordFile: String;
  Code: Integer;
begin
  LogFolderChosen := LogDir;
  Args := PythonSafeFlag + '-m il2ks --config ' + QuoteArg(ConfigPath) + ' setup --non-interactive --data-dir ' + QuoteArg(DataDir) + ' --https ' + Mode;
  if LogDir <> '' then
    Args := Args + ' --logs-dir ' + QuoteArg(LogDir);
  if TimeZone <> '' then
    Args := Args + ' --timezone ' + QuoteArg(TimeZone);
  if Domain <> '' then
    Args := Args + ' --domain ' + QuoteArg(Domain);
  if Email <> '' then
    Args := Args + ' --email ' + QuoteArg(Email);
  PasswordFile := Param('ADMINPASSWORDFILE');  // silent installs: the caller's own file (deleted after use)
  if (PasswordFile = '') and (AdminPassword <> '') then
  begin
    PasswordFile := WritePasswordFile(AdminPassword);
  end;
  if PasswordFile <> '' then
    Args := Args + ' --admin-username ' + QuoteArg(AdminUser) + ' --admin-password-file ' + QuoteArg(PasswordFile)
  else
    Args := Args + ' --no-admin';
  Code := RunCaptured(PythonExe, Args, Output);
  if PasswordFile <> '' then
    DeleteFile(PasswordFile);
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
  // WinSW registers the service as LocalSystem; switch it to the virtual account (no password: Windows manages it).
  Code := RunCaptured(ExpandConstant('{sys}\sc.exe'), 'config ' + ServiceId + ' obj= "' + ServiceAccount + '"', Output);
  Log('sc config obj: ' + IntToStr(Code) + #13#10 + Output);
  if Code <> 0 then
  begin
    Warn('The service account ' + ServiceAccount + ' could not be set (exit code ' + IntToStr(Code) + '):' + #13#10 + LastLines(Output, 5));
    Result := False;
    Exit;
  end;
  GrantServiceAccount;
  Code := RunCaptured(ServiceExe, 'start', Output);
  Log('il2ks-service start: ' + IntToStr(Code) + #13#10 + Output);
  Result := Code = 0;
  if not Result then
    Warn('The service was installed but did not start (exit code ' + IntToStr(Code) + '). ' +
         'Run "Run doctor" from the Start menu, and read the newest files in ' + DataDir + '\logs.');
end;

// --- upgrade: warn about customized templates that the new version changed (TD-25) ----------------------------------------

// Writes to the installer log and, in the data folder's logs\, to installer-custom-check.log (so it survives the installer log).
procedure NoteCustomCheck(const Text: String);
begin
  Log(Text);
  ForceDirectories(DataDir + '\logs');
  SaveStringToFile(DataDir + '\logs\installer-custom-check.log',
                   AnsiString(GetDateTimeString('yyyy-mm-dd hh:nn:ss', '-', ':') + ' ' + Text + #13#10), True);
end;

// Only items of the report: `il2ks custom list --problems` prints "STATE  path" unindented, the explanation indented.
function CustomCheckSummary(const Output: String; var Count: Integer): String;
var
  Rest, Line: String;
  P: Integer;
begin
  Result := '';
  Count := 0;
  Rest := Output;
  while Rest <> '' do
  begin
    P := Pos(#10, Rest);
    if P = 0 then
    begin
      Line := Rest;
      Rest := '';
    end
    else
    begin
      Line := Copy(Rest, 1, P - 1);
      Rest := Copy(Rest, P + 1, Length(Rest));
    end;
    Line := TrimRight(Line);
    if (Line <> '') and (Line[1] <> ' ') then
    begin
      Count := Count + 1;
      if Count <= 8 then
        Result := Result + '   ' + Line + #13#10;
    end;
  end;
  if Count > 8 then
    Result := Result + '   ... and ' + IntToStr(Count - 8) + ' more' + #13#10;
end;

// Runs after an upgrade (not a fresh install: that has no overrides). Exit code 4 of --fail-on-problems means "some override is out
// of date"; any other failure is only logged. Never fails the install; silent installs get log lines instead of a message box.
procedure CheckCustomOverrides;
var
  Output, Summary, Text: String;
  Code, Count: Integer;
begin
  if not (FileExists(ConfigPath) and FileExists(PythonExe)) then
    Exit;
  Code := RunCaptured(PythonExe, PythonSafeFlag + '-m il2ks --config ' + QuoteArg(ConfigPath) + ' custom list --problems --fail-on-problems', Output);
  Log('il2ks custom list --problems: exit code ' + IntToStr(Code) + #13#10 + Output);
  if Code = 0 then
    Exit;
  if Code <> 4 then
  begin
    NoteCustomCheck('The check of your customized templates did not run (exit code ' + IntToStr(Code) + '): ' + LastLines(Output, 3));
    Exit;
  end;
  Summary := CustomCheckSummary(Output, Count);
  NoteCustomCheck(IntToStr(Count) + ' customized file(s) in ' + DataDir + '\custom need attention after the upgrade:' + #13#10 + Trim(Output));
  if WizardSilent then
    Exit;
  Text := 'The upgrade changed built-in pages that you have customized (' + IntToStr(Count) + ' file(s) in ' + DataDir + '\custom). ' +
          'Your versions are still used, but they may miss new content or break:' + #13#10 + #13#10 + Summary + #13#10 +
          'What to do, in the il2ks command prompt (Start menu):' + #13#10 +
          '   il2ks custom list              the full list with explanations' + #13#10 +
          '   il2ks custom diff <path>       what differs from the new built-in file' + #13#10 +
          '   il2ks custom accept <path>     once your file is up to date (stops this warning)' + #13#10 + #13#10 +
          'Details: docs/customizing.md in the il2ks documentation. The full report is in ' + DataDir + '\logs\installer-custom-check.log.';
  MsgBox(Text, mbInformation, MB_OK);
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
  if HaveConfig then
    CheckCustomOverrides;
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
  // Never validate in a silent run: the page edits are empty there (answers come from /ADMINPASSWORDFILE etc.), a failed check
  // would only show a suppressed message box and leave the wizard on that page forever.
  if WizardSilent then
    Exit;
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
    Code := RunCaptured(PythonExe, PythonSafeFlag + '-m il2ks --config ' + QuoteArg(ConfigPath) + ' backup', Output);
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
