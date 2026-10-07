# Using your own HTTPS proxy (nginx, IIS, ...)

By default `il2ks run` starts Caddy, which handles HTTPS. If ports 80 and 443 are already used by another web server
(IIS, nginx, Apache) on the same machine, or you simply prefer your own, use **external mode**: you run the proxy,
il2ks serves the site on the local port `8000`.

```toml
# il2ks.toml
[https]
mode = "external"              # il2ks does not start Caddy
domain = "stats.example.com"   # the name your proxy serves: il2ks only accepts requests for this name

[web]
host = "127.0.0.1"             # keep it: only the proxy on this machine may reach the web server
port = 8000
```

`il2ks run` then supervises the web server and the log watcher only. Your proxy has to:

1. **Terminate HTTPS** (certificate on the proxy) and redirect plain http to https.
2. **Forward everything** to `http://127.0.0.1:8000`.
3. Send the header **`X-Forwarded-Proto: https`**. Without it il2ks thinks the visit is plain http and redirects
   forever ("too many redirects"). il2ks only trusts this header, so keep port 8000 closed to the outside.
4. Send **`X-Forwarded-For`** with the visitor's address (nginx `$proxy_add_x_forwarded_for`; Apache's `mod_proxy` and
   IIS ARR add it by default). The admin-login lockout (5 wrong passwords lock the account and the visitor's address for 15 minutes, see
   [settings.md](settings.md#other-sections)) counts failed passwords per visitor address. il2ks believes
   this header only when the connection comes from the same machine (127.0.0.1 / ::1), so the web server must stay on
   `host = "127.0.0.1"`: if it were reachable from outside, a visitor could invent the header. Without the header from
   your proxy, all visitors share one address (five wrong passwords from anyone lock that address for everybody, for 15
   minutes; the account lock still protects the password).
5. Keep the original **`Host`** header (recommended) so links and the admin login work.
6. **Compress the pages** (gzip or better). il2ks already sends its stylesheets, scripts and icons compressed, but the
   pages themselves come uncompressed, and the bundled Caddy normally compresses them. Pages with long tables shrink
   about five to ten times, which phones on mobile data notice. The samples below include it.

With the proxy running, `il2ks doctor` shows whether ports and settings agree.

## nginx

```nginx
# /etc/nginx/conf.d/il2ks.conf
server {
    listen 80;
    server_name stats.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name stats.example.com;

    ssl_certificate     /etc/letsencrypt/live/stats.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/stats.example.com/privkey.pem;

    gzip on;
    gzip_proxied any;   # nginx skips proxied responses without this
    gzip_types text/css application/javascript application/json image/svg+xml text/plain;   # text/html is always on

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host              $host;
        proxy_set_header X-Forwarded-Proto $scheme;     # "https" here
        proxy_set_header X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header X-Real-IP         $remote_addr;
    }
}
```

Get the certificate with certbot (`sudo certbot --nginx -d stats.example.com` writes the two `ssl_` lines for you).
Then `sudo nginx -t && sudo systemctl reload nginx`.

## IIS (Windows)

You need two IIS add-ons, free from Microsoft: **URL Rewrite** and **Application Request Routing (ARR)**. Both are in
the *Web Platform Installer* replacement downloads at <https://www.iis.net/downloads>.

1. **Turn the proxy on:** IIS Manager, click the server name (top of the left tree), open *Application Request
   Routing Cache*, then *Server Proxy Settings* on the right, tick **Enable proxy**, *Apply*.
2. **Keep the Host header:** in an Administrator command prompt:

   ```
   %windir%\system32\inetsrv\appcmd.exe set config -section:system.webServer/proxy /preserveHostHeader:"True" /commit:apphost
   ```
3. **Site and certificate:** create (or reuse) an IIS site with the host name `stats.example.com`, a binding for
   `https` on 443 with your certificate (for a free one, use *win-acme*, <https://www.win-acme.com>), and a binding
   on `http` port 80.
4. **Allow the forwarded-proto header:** select the site, open *URL Rewrite*, click *View Server Variables...* on the
   right, *Add...*, name **`HTTP_X_FORWARDED_PROTO`**, *OK*.
5. **Compression:** add the Windows feature *Dynamic Content Compression* (Server Manager, *Add Roles and Features*,
   Web Server (IIS) > Web Server > Performance; on Windows 10/11: *Turn Windows features on or off*, Internet
   Information Services > World Wide Web Services > Performance Features). The `web.config` below switches it on for
   the site.
6. **The rules.** Put this `web.config` into the site's folder (the first rule redirects http to https, the second
   forwards to il2ks):

```xml
<?xml version="1.0" encoding="UTF-8"?>
<configuration>
  <system.webServer>
    <urlCompression doStaticCompression="true" doDynamicCompression="true" />
    <rewrite>
      <rules>
        <rule name="http to https" stopProcessing="true">
          <match url="(.*)" />
          <conditions>
            <add input="{HTTPS}" pattern="^OFF$" />
          </conditions>
          <action type="Redirect" url="https://{HTTP_HOST}/{R:1}" redirectType="Permanent" />
        </rule>
        <rule name="il2ks" stopProcessing="true">
          <match url="(.*)" />
          <serverVariables>
            <set name="HTTP_X_FORWARDED_PROTO" value="https" />
          </serverVariables>
          <action type="Rewrite" url="http://127.0.0.1:8000/{R:1}" />
        </rule>
      </rules>
    </rewrite>
  </system.webServer>
</configuration>
```

Test: `https://stats.example.com` shows the site; `http://stats.example.com` redirects to https. If the admin login
says "CSRF verification failed", check that `[https] domain` in `il2ks.toml` is exactly the name in the browser's
address bar.

## Other proxies (Apache, Traefik, HAProxy, ...)

Same requirements. For Apache with `mod_proxy`, `mod_headers` and `mod_deflate`:

```apache
RequestHeader set X-Forwarded-Proto "https"
ProxyPreserveHost On
ProxyPass        / http://127.0.0.1:8000/
ProxyPassReverse / http://127.0.0.1:8000/
AddOutputFilterByType DEFLATE text/html text/css application/javascript application/json image/svg+xml text/plain
```

## Not on the same machine as the proxy?

If the proxy runs on another machine, set `[web] host = "0.0.0.0"` (or that machine's LAN address) and **firewall the
port** so only the proxy can connect. `il2ks doctor` warns about this on purpose: anyone who can reach the port can
bypass your HTTPS.

## Monitoring: `/healthz`

For an uptime monitor (Uptime Kuma, UptimeRobot, a Docker or Kubernetes health check, a plain `curl` in cron) the site has
`/healthz`:

- **200** with the body `ok` when il2ks can read its database, **503** with `database unavailable` when it cannot.
- No login, no cookies, `Cache-Control: no-store`: never cached by il2ks, your proxy should not cache it either.
- It reads one row of the database, nothing else, so it is cheap enough to ask every few seconds. It tells you that the
  web server is up and the database opens; it does not tell you that the statistics are fresh (check the "Data updated"
  line in the page footer, or `il2ks doctor`, for that).

Two places to ask, depending on what you want to know:

| Address | What it proves |
|---|---|
| `https://stats.example.com/healthz` | the whole chain: DNS, certificate, proxy, web server, database |
| `http://127.0.0.1:8000/healthz` (from the same machine) | web server and database only. Plain http works here: `/healthz` is the one page that is never redirected to https |

The bundled Caddy forwards `/healthz` like any other page. With your own proxy, make sure no rule in front of the
site (a login, a cache, a "maintenance" redirect) swallows it.

## Are the pages compressed?

`il2ks doctor` fetches the home page through your public address (`[https] domain`), asking for `gzip, br, zstd`, and
warns when the answer has no `Content-Encoding` header: your proxy is not compressing (requirement 6 above). It skips
the check when no domain is set or when the bundled Caddy is used (it always compresses). A fetch that fails (the machine
cannot reach its own public address, a certificate this machine does not trust) is only a warning, never an error.
