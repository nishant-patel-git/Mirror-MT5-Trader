<#
    Build MT5-golden.zip - the stripped MetaTrader 5 folder every office
    PC gets two copies of.

    You run this ONCE, on the machine where you installed and tuned one
    terminal. SETUP.bat then unpacks the result twice on every PC, which
    is faster than running the broker's installer twice and - the part
    that matters - identical every time. No installer dialogs, no
    'did I tick the same boxes on both'.

    Before you run it, prepare the source terminal by hand:

      1. Install MetaTrader 5 once, into its own folder.
      2. Log in, so the servers list knows your broker, then LOG OUT.
      3. Market Watch: keep only the symbols this desk trades.
      4. Close every chart. Charts are the bulk of the zip and every
         one of them is a subscription the terminal maintains.
      5. Tools > Options > Server: turn off News. Community and
         Signals too.
      6. Tools > Options > Expert Advisors: tick Allow Algo Trading.
         Baked in here, it is one less thing for a trader to be told.
      7. Close the terminal. A running terminal has files open and the
         copy below will be a snapshot of a half-written state.

    Then:

        .\deploy\make-golden-terminal.ps1 -Source 'C:\MT5-A'

    Re-run it whenever the broker ships a new terminal build. Whoever
    owns that decision owns this file.
#>

[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)]
    [string] $Source,

    [string] $Output = (Join-Path $PSScriptRoot 'MT5-golden.zip'),

    # Belt and braces. The check below refuses to zip a terminal that
    # still has an account in it; this is the switch for the one case
    # where you have looked and you are sure.
    [switch] $Force
)

$ErrorActionPreference = 'Stop'

if (-not (Test-Path (Join-Path $Source 'terminal64.exe'))) {
    throw ($Source + ' has no terminal64.exe in it. Point -Source at the ' +
           'MetaTrader 5 FOLDER, not the shortcut and not Program Files.')
}
if (Get-Process -Name 'terminal64' -ErrorAction SilentlyContinue) {
    throw ('MetaTrader 5 is running. Close it first - a zip taken while ' +
           'the terminal is open is a snapshot of half-written files.')
}

# Everything is copied to a staging folder and stripped THERE. The
# source is somebody's working terminal and this script must not be
# able to delete anything in it.
$stage = Join-Path $env:TEMP ('mt5-golden-' + [Guid]::NewGuid().ToString('N'))
Write-Host ('Staging in ' + $stage)
Copy-Item -Recurse -Force -Path $Source -Destination $stage

<#
    What comes out, and why.

    What comes OUT:

    logs / Logs      Every one names an account number and a server. A
                     template that carries them ships one trader's
                     account details to every PC in the office.
    accounts.dat     The saved logins, and on some builds the saved
                     password. THIS is the file that must never travel.
    bases, history   Downloaded price history. Gigabytes, and rebuilt
                     from the server on first connect anyway.
    profiles\*       Saved chart layouts. Each chart is a live
                     subscription the terminal re-opens on startup.
    MQL5\Logs        Same as logs, from the expert side.

    What deliberately STAYS, and why stripping it breaks the install:

    servers.dat      The list of brokers the terminal knows. This is
                     what the 'Open an Account' dialog fills from, and
                     it is the whole reason the source terminal is
                     logged in once before being zipped. Remove it and
                     'MentoMarkets-Server' means nothing on the new PC:
                     mt5.initialize(server=...) cannot resolve a server
                     the terminal has never heard of, and the trader is
                     back to picking Mento Markets Ltd. from a list -
                     which is exactly the step this setup removes.
    common.ini,      The Options settings, INCLUDING Allow Algo
    terminal.ini     Trading. Baking that in is step 6 of the prep
                     above; deleting these files throws it away and
                     every PC needs the button pressed by hand.

    Neither holds a password. accounts.dat is the one that does, and it
    is checked for by name below.
#>
$strip = @(
    'logs', 'Logs', 'bases', 'history',
    'MQL5\Logs', 'MQL5\Files', 'MQL5\Images',
    'profiles\default\chart01.chr', 'profiles\charts'
)
foreach ($relative in $strip) {
    $path = Join-Path $stage $relative
    if (Test-Path $path) {
        Write-Host ('  removing ' + $relative)
        Remove-Item -Recurse -Force $path
    }
}

# Credentials, specifically, and ONLY these. Named separately from the
# bulk above because this is the check that has to be right - in both
# directions. A file that stays here by mistake ships a login; a file
# removed by mistake (servers.dat, terminal.ini) ships a terminal that
# cannot find the broker or has Algo Trading switched off again.
$secrets = @()
foreach ($name in @('accounts.dat', 'accounts.ini')) {
    $secrets += Get-ChildItem -Path $stage -Filter $name -Recurse -Force `
                              -ErrorAction SilentlyContinue
}
foreach ($file in $secrets) {
    Write-Host ('  removing ' + $file.FullName.Substring($stage.Length + 1))
    Remove-Item -Force $file.FullName
}

$left = Get-ChildItem -Path $stage -Filter 'accounts.*' -Recurse -Force `
                      -ErrorAction SilentlyContinue
if ($left -and -not $Force) {
    # The join is computed FIRST, on its own line. Inline, '+' binds
    # tighter than -join and the message comes out as the separator
    # joining the sentence - which is a refusal nobody can read.
    $names = ($left | ForEach-Object { $_.Name }) -join ', '
    Remove-Item -Recurse -Force $stage
    throw ('An accounts file survived the strip: ' + $names +
           '. Refusing to build a template that might carry a login to ' +
           'every PC in the office. Log the terminal out and try again.')
}

# And the check the other way. A terminal that was never logged in has
# no servers list, so 'MentoMarkets-Server' will not resolve on any PC
# this zip is unpacked onto - and the failure appears at the first
# connect, on a trader's machine, as a login that will not go through.
$servers = Get-ChildItem -Path $stage -Filter 'servers.dat' -Recurse -Force `
                         -ErrorAction SilentlyContinue
if (-not $servers) {
    Remove-Item -Recurse -Force $stage
    throw (
        'There is no servers.dat under ' + $Source + ', so this zip would ' +
        'carry no broker list. TWO different things cause that, and they ' +
        'need different answers:' + [Environment]::NewLine +
        [Environment]::NewLine +
        '  1. The terminal was never logged in. Open it, pick the broker ' +
        'in Open an Account, log in once, log out, close it, re-run this.' +
        [Environment]::NewLine +
        [Environment]::NewLine +
        '  2. MORE LIKELY: this is a NORMAL (non-portable) install, and ' +
        'MetaTrader 5 keeps its settings in ' +
        '%APPDATA%\MetaQuotes\Terminal\<a long hex folder>\ rather than ' +
        'in the program folder. Look there: if you find config\servers.dat ' +
        'in one of those, that is where the settings live and copying the ' +
        'program folder alone cannot carry them.' +
        [Environment]::NewLine +
        [Environment]::NewLine +
        'Do NOT reach for /portable to force the files into one place: a ' +
        'terminal started portable refuses IPC from a normally-started ' +
        'Python, so the legs would never connect. Say which of the two it ' +
        'is and the setup can be built the right way round.')
}
Write-Host ('  keeping ' + $servers[0].Name + ' (the broker list)')

if (Test-Path $Output) { Remove-Item -Force $Output }
Write-Host ('Compressing to ' + $Output + ' ...')
# The CONTENTS, not the folder: SETUP unpacks straight into C:\MT5-A,
# and a zip of the folder would land terminal64.exe one level too deep.
Compress-Archive -Path (Join-Path $stage '*') -DestinationPath $Output
Remove-Item -Recurse -Force $stage

$mb = [Math]::Round((Get-Item $Output).Length / 1MB, 1)
Write-Host ''
Write-Host ('Done: ' + $Output + '  (' + $mb + ' MB)') -ForegroundColor Green
Write-Host 'Copy it beside SETUP.bat and setup.ps1.'
if ($mb -gt 300) {
    Write-Host ''
    Write-Host ('  [!] That is large for a stripped terminal. Check the ' +
                'charts are closed and the history folder is gone.') `
               -ForegroundColor Yellow
}
