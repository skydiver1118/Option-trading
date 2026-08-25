# Windows local scheduler for Option-trading

This package makes the local Windows PC the primary scheduler while keeping GitHub as the publishing/version-control layer.

## Schedule

Windows Task Scheduler runs the dashboard refresh at 10:00 AM, 12:00 PM, and 2:00 PM on weekdays. The runner independently checks the NYSE calendar and exits on market holidays.

## Requirements

- Windows 10/11
- Python available as `python`
- Git available as `git`
- Repository cloned locally over HTTPS
- GitHub authentication available through Git Credential Manager or GitHub Desktop
- Tradier production API token
- Windows user remains logged in. The remote session can be disconnected; the task still runs. The task is configured to wake a sleeping PC, but cannot run while the PC is powered off.

## Fast setup on a remote PC

You do not need to transfer a ZIP from the iPhone. Use the iPhone remote-control app to open PowerShell on the Windows PC, then clone or update the GitHub repository directly on that PC.

### If the repository is not yet on the PC

```powershell
cd $HOME\Documents
git clone https://github.com/skydiver1118/Option-trading.git
cd Option-trading
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1
```

### If the repository already exists on the PC

```powershell
cd C:\path\to\Option-trading
git pull
powershell -ExecutionPolicy Bypass -File .\windows\install.ps1
```

The installer will:

1. install Python dependencies;
2. prompt for the Tradier token;
3. DPAPI-encrypt the token for the current Windows user and PC;
4. verify Tradier access;
5. verify GitHub push authentication;
6. create three Windows Scheduled Tasks;
7. run a forced end-to-end test refresh.

## Security

The Tradier token is stored at `windows/.secrets/tradier_token.txt` using Windows DPAPI encryption. It is decryptable only under the same Windows user profile on the same PC. The plaintext token exists only temporarily in the refresh process environment.

Do not copy the encrypted token file to another computer; rerun `install.ps1` and enter the token there instead.

## What each scheduled run does

1. verifies that today is an NYSE trading day;
2. tries to `git pull --rebase`;
3. loads the encrypted Tradier token;
4. runs `scripts/update_dashboard.py`;
5. verifies `data/dashboard.json` was freshly generated;
6. commits the new JSON;
7. retries `git push` up to four times;
8. GitHub's push-triggered `pages.yml` publishes the dashboard.

The local PC therefore controls the timing. GitHub Actions cron remains only as a backup. Push-triggered GitHub Pages deployment is retained because push events are normally much more prompt than cron scheduling.

## Logs

Logs are written to:

`windows/logs/refresh_YYYY-MM-DD_HHMMSS.log`

The runner retains the latest 60 logs.

## Test manually

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\run_refresh.ps1 -Force
```

## Inspect tasks

```powershell
Get-ScheduledTask -TaskName 'OptionDashboard-*' | Get-ScheduledTaskInfo
```

## Remove the scheduled tasks

```powershell
powershell -ExecutionPolicy Bypass -File .\windows\uninstall_tasks.ps1
```

This removes the tasks but leaves the encrypted Tradier token and logs in place.
