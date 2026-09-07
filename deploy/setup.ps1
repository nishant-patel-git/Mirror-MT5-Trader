<#
    MT5-Trader: turn a bare Windows PC into a trading box.

    Launched by SETUP.bat, which has already asked for Administrator.
    Run it directly only if you know why:

        Set-ExecutionPolicy -Scope Process Bypass -Force
        .\deploy\setup.ps1 -RepoUrl https://github.com/OWNER/REPO.git

    The order is deliberate. Everything that can fail cheaply fails
    FIRST: the golden zip is checked before Git is downloaded, and the
    tests run before the trader is asked for a single password. A setup
    that is going to fail should fail in the first minute, in front of
    whoever started it - not at 9am the next morning.

    Plain ASCII and single quotes throughout, for the reason bootstrap.ps1
    gives: Windows PowerShell 5.1 reads a script with no byte-order mark
    as ANSI, and one curly quote from a word processor comes back as a
    parser error on a line that looks perfectly fine.
#>

[CmdletBinding()]
param(
    # Where the code lands. Short, no spaces, off the profile: a path
    # with a space in it is the thing that breaks a .bat six months later.
    #
    # These four default to whatever rollout.json says, and rollout.json
    # is THE place to change the repository and branch for the office.
    # A blank here means 'take it from the file'; passing one on the
    # command line overrides the file for this one machine.
    #
    [string] $Root = '',

    [string] $RepoUrl = '',
    [string] $Branch = '',

    # A fine-grained, READ-ONLY token for this one repository. Read-only
    # on purpose: a token that leaks off an office PC then reads code,
    # it cannot push. Leave it blank to be prompted, or to use a machine
    # that is already signed in to GitHub.
    [string] $Token = '',
    [string] $TokenUser = 'x-access-token',

    # The stripped MetaTrader 5 folder, zipped. Made once, by
    # make-golden-terminal.ps1, and copied beside this script.
    [string] $GoldenZip = '',

    # TWO folders, never one folder and a shortcut. A terminal holds a
    # single login, so two runners on one installation are one account
    # trading against itself.
    [string] $TerminalA = '',
    [string] $TerminalB = '',

    # For a re-run on a machine that is already set up: keep its
    # config.json rather than asking the six questions again.
    [switch] $KeepConfig
)

$ErrorActionPreference = 'Stop'
$here = $PSScriptRoot

<#
    Anything that throws lands here: the REASON in red, and exit 1 so
    SETUP.bat knows it failed.

    Without this the installer shows a PowerShell stack trace - which
    to the person standing at the machine is indistinguishable from
    success followed by noise - and, on some hosts, still exits 0.
#>
trap {
    Write-Host ''
    Write-Host ('  [X] ' + $_.Exception.Message) -ForegroundColor Red
    Write-Host ''
    exit 1
}

function Say    { param([string] $m) Write-Host ('  ' + $m) }
function Step   { param([string] $m) Write-Host ''; Write-Host ('== ' + $m) -ForegroundColor Cyan }
function Warn   { param([string] $m) Write-Host ('  [!] ' + $m) -ForegroundColor Yellow }
function Fail   { param([string] $m) throw $m }

Write-Host ''
Write-Host '  =====================================================' -ForegroundColor Cyan
Write-Host '    MT5-Trader - setting up this PC' -ForegroundColor Cyan
Write-Host '  =====================================================' -ForegroundColor Cyan

# --- 0. The cheap checks, before anything is downloaded -----------------

Step 'Checking what this setup was given'

<#
    rollout.json holds the repository, the branch and the folders, so
    moving the office from the demo repo to the live one is an edit to
    one file in the rollout kit rather than a flag somebody has to
    remember on every machine.

    A missing file is not fatal - the defaults below keep a bare
    SETUP.bat working - but it IS said out loud, because a setup that
    silently installed from the wrong repository would be found out
    weeks later.
#>
$rolloutPath = Join-Path $here 'rollout.json'
$rollout = $null
if (Test-Path $rolloutPath) {
    try {
        $rollout = Get-Content -Raw -Path $rolloutPath | ConvertFrom-Json
        Say ('Rollout settings: ' + $rolloutPath)
    } catch {
        Fail ('rollout.json is next to this script but will not parse (' +
              $_.Exception.Message + '). Fix the file rather than ' +
              'deleting it - installing from a guessed repository is ' +
              'worse than not installing.')
    }
} else {
    Warn ('No rollout.json beside this script - using built-in defaults. ' +
          'Copy the whole deploy folder into the rollout kit so the ' +
          'repository and branch come from one place.')
}

function Setting {
    param([string] $Given, [string] $Key, [string] $Default)
    if ($Given) { return $Given }
    if ($rollout -and $rollout.PSObject.Properties.Name -contains $Key) {
        $value = [string] $rollout.$Key
        if ($value) { return $value }
    }
    return $Default
}

$RepoUrl   = Setting $RepoUrl   'repo_url' 'https://github.com/nishant-patel-git/Mirror-MT5-Trader.git'
$Branch    = Setting $Branch    'branch'   'main'
$Root      = Setting $Root      'root'       'C:\MT5-Trader'
$TerminalA = Setting $TerminalA 'terminal_a' 'C:\MT5-A'
$TerminalB = Setting $TerminalB 'terminal_b' 'C:\MT5-B'
# A LIST, not one number: 3.11 and 3.14 have both run the whole suite
# green, and refusing a machine that already has a proven interpreter
# would mean installing a second Python on every desk for nothing.
# Anything not on the list is still refused, so a stray 3.9 cannot
# quietly become the one PC that behaves differently.
$PyVersions = @()
if ($rollout -and $rollout.PSObject.Properties.Name -contains 'python_versions') {
    $PyVersions = @($rollout.python_versions)
}
if (-not $PyVersions) { $PyVersions = @('3.11', '3.14') }

Say ('Repository:        ' + $RepoUrl)
Say ('Branch:            ' + $Branch)

if (-not $GoldenZip) {
    $candidate = Join-Path $here 'MT5-golden.zip'
    if (Test-Path $candidate) { $GoldenZip = $candidate }
}
if (-not $GoldenZip -or -not (Test-Path $GoldenZip)) {
    Fail ('MT5-golden.zip was not found beside this script. That zip is ' +
          'the stripped MetaTrader 5 folder every PC gets a copy of; ' +
          'make it once with make-golden-terminal.ps1 and copy it here. ' +
          'Without it this machine would have no terminals to trade ' +
          'through.')
}
if ($TerminalA.TrimEnd('\') -ieq $TerminalB.TrimEnd('\')) {
    Fail ('TerminalA and TerminalB are the same folder. One MetaTrader 5 ' +
          'installation holds ONE login, so both legs would end up on the ' +
          'same account and the pair would hedge against itself.')
}
Say ('Terminal template: ' + $GoldenZip)
Say ('Code folder:       ' + $Root)

# --- 1. Git --------------------------------------------------------------

Step 'Git'

function Refresh-Path {
    $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') +
                ';' + [Environment]::GetEnvironmentVariable('Path', 'User')
}

if (Get-Command git -ErrorAction SilentlyContinue) {
    Say ('Already installed: ' + (git --version))
} else {
    Say 'Installing Git for Windows...'
    $gitExe = Join-Path $env:TEMP 'git-setup.exe'
    Invoke-WebRequest -UseBasicParsing -OutFile $gitExe -Uri (
        'https://github.com/git-for-windows/git/releases/download/' +
        'v2.45.2.windows.1/Git-2.45.2-64-bit.exe')
    Start-Process -Wait -FilePath $gitExe -ArgumentList (
        '/VERYSILENT /NORESTART /NOCANCEL /SP- /SUPPRESSMSGBOXES ' +
        '/COMPONENTS="icons,ext\shellhere,assoc,assoc_sh"')
    Refresh-Path
    if (-not (Get-Command git -ErrorAction SilentlyContinue)) {
        Fail ('Git installed but is still not on PATH. Close this window, ' +
              'open a new one, and run SETUP.bat again.')
    }
    Say ('Installed: ' + (git --version))
}

# --- 2. Python -----------------------------------------------------------

Step 'Python'

function Test-Python {
    <#
        Is this interpreter one this project can actually run on?

        Two things are checked and NEITHER is negotiable:

        64-BIT. MetaTrader5's IPC handshake fails against a 32-bit
        Python with an error that says nothing useful, and the symptom
        on the desk is a leg that never connects.

        THE VERSION. The suite is tested on 3.11. A machine carrying
        the company's Python 3.9, or a 3.13 that no wheel exists for
        yet, must be told so - not quietly used, which turns one PC
        into the one that behaves differently.
    #>
    param([string[]] $Command)
    $probe = 'import sys, struct; print("%d.%d %d" % (sys.version_info[0], sys.version_info[1], struct.calcsize("P") * 8))'
    $out = Invoke-Python $Command @('-c', $probe) 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $out) { return $null }
    $parts = ([string] $out).Trim() -split ' '
    return @{ Version = $parts[0]; Bits = [int] $parts[1];
              Command = $Command }
}

function Find-Python {
    <#
        The command that runs Python here, as a list: the program and
        any arguments it needs. Three ways a working Python turns up on
        these boxes and all three are normal - the py launcher, a plain
        'python' on PATH (what a conda or venv prompt has), or neither.
        Assuming the launcher is why a machine that already had Python
        was told it had none.

        The exact version is asked for FIRST. 'py -3.11' on a box that
        also has 3.9 on PATH is the good outcome, and looking at PATH
        first would miss it.
    #>
    foreach ($version in $PyVersions) {
        if (Get-Command py -ErrorAction SilentlyContinue) {
            $found = Test-Python @('py', ('-' + $version))
            if ($found) { return $found }
        }
    }
    if (Get-Command python -ErrorAction SilentlyContinue) {
        $found = Test-Python @('python')
        if ($found) { return $found }
    }
    return $null
}

function Assert-Python {
    <#
        The loud refusal. A wrong Python is a machine that will behave
        differently from every other desk, so it stops the install and
        names what to do about it.
    #>
    param($Found)
    if ($Found.Bits -ne 64) {
        Fail ('This machine''s Python is ' + $Found.Bits + '-bit (' +
              ($Found.Command -join ' ') + ', version ' + $Found.Version +
              '). MetaTrader 5 will not talk to it: the handshake fails ' +
              'with an error that says nothing, and the leg simply never ' +
              'connects. Uninstall it, or install Python ' +
              $PyVersions[0] + ' 64-bit from python.org alongside it, and ' +
              'run SETUP.bat again.')
    }
    if ($PyVersions -notcontains $Found.Version) {
        Fail ('This machine has Python ' + $Found.Version + ' (' +
              ($Found.Command -join ' ') + '), and the suite has only been ' +
              'run on ' + ($PyVersions -join ' and ') + '. Refusing to ' +
              'install onto an untested one rather than making this the ' +
              'PC that behaves differently from every other desk. Install ' +
              $PyVersions[0] + ' 64-bit from python.org - ticking "Add ' +
              'python.exe to PATH" and "py launcher" - and run SETUP.bat ' +
              'again. The existing Python can stay; the launcher picks ' +
              'the right one. If this version HAS been proven, add it to ' +
              'python_versions in rollout.json.')
    }
}

function Invoke-Python {
    param([string[]] $Command, [string[]] $Arguments)
    $exe = $Command[0]
    $argv = @()
    if ($Command.Count -gt 1) { $argv += $Command[1..($Command.Count - 1)] }
    $argv += $Arguments
    & $exe @argv
}

$found = Find-Python
if ($null -eq $found) {
    if ($PyVersions -notcontains '3.11') {
        Fail ('This machine has no Python, and rollout.json does not list ' +
              '3.11 - the only version this script knows how to fetch ' +
              'unattended. Install one of ' + ($PyVersions -join ', ') +
              ' 64-bit by hand and run SETUP.bat again.')
    }
    # 64-bit, and it must match the 64-bit terminal: a 32-bit Python
    # fails the MT5 IPC handshake with an error that says nothing.
    # Include_tcltk: the setup wizard is a tkinter window.
    Say 'Installing Python 3.11, 64-bit...'
    $pyExe = Join-Path $env:TEMP 'python-3.11.exe'
    Invoke-WebRequest -UseBasicParsing -OutFile $pyExe -Uri (
        'https://www.python.org/ftp/python/3.11.9/python-3.11.9-amd64.exe')
    Start-Process -Wait -FilePath $pyExe -ArgumentList (
        '/quiet InstallAllUsers=1 PrependPath=1 Include_test=0 Include_tcltk=1')
    Refresh-Path
    $found = Find-Python
    if ($null -eq $found) {
        Fail ('Python installed but is still not on PATH. Close this ' +
              'window, open a new one, and run SETUP.bat again.')
    }
}
Assert-Python $found
$python = $found.Command
Say ('Using Python: ' + ($python -join ' ') + '  (version ' +
     $found.Version + ', ' + $found.Bits + '-bit)')

# tkinter is what the six-question window is drawn with. Checked here,
# where it is still fixable, rather than at the moment the wizard opens.
Invoke-Python $python @('-c', 'import tkinter') 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Warn ('This Python has no tkinter, so the setup window cannot open. ' +
          'The rest of the install will run and you can configure the ' +
          'machine from the Exchanges page instead.')
}

# --- 3. The code ---------------------------------------------------------

Step 'The code'

if ($Token) {
    <#
        Into the Windows Credential Manager, NOT into the clone URL.
        A token in the URL is written to .git\config in plain text and
        is then in every screenshot of that file forever; this way the
        remote stays clean and Windows holds the secret.
    #>
    $uri = [Uri] $RepoUrl
    $blob = ("protocol=" + $uri.Scheme + "`nhost=" + $uri.Host +
             "`nusername=" + $TokenUser + "`npassword=" + $Token + "`n`n")
    git config --global credential.helper manager | Out-Null
    $blob | git credential approve
    Say 'Repository token stored in Windows Credential Manager.'
}

if (Test-Path (Join-Path $Root '.git')) {
    Say 'Already cloned - fetching the latest instead.'
    Push-Location $Root
    git fetch --quiet origin $Branch
    # --ff-only: if this machine somehow has local commits, say so
    # rather than starting a merge nobody is here to finish.
    git merge --ff-only ('origin/' + $Branch)
    if ($LASTEXITCODE -ne 0) {
        Warn ('This clone has local changes that cannot fast-forward. ' +
              'Carrying on with the code already here.')
    }
    Pop-Location
} else {
    if (Test-Path $Root) {
        $existing = Get-ChildItem -Force $Root
        if ($existing) {
            Fail ($Root + ' already exists and is not a clone. Move it ' +
                  'aside and run SETUP.bat again - overwriting it might ' +
                  'destroy a config or a book this desk still needs.')
        }
    }
    Say ('Cloning into ' + $Root + ' ...')
    git clone --quiet --branch $Branch $RepoUrl $Root
    if ($LASTEXITCODE -ne 0) {
        Fail ('The clone failed. If the repository is private, re-run ' +
              'SETUP.bat with -Token followed by a fine-grained ' +
              'read-only token for it.')
    }
}

Step 'Dependencies'
Push-Location $Root
Invoke-Python $python @('-m', 'pip', 'install', '--upgrade', '--quiet', 'pip')
Invoke-Python $python @('-m', 'pip', 'install', '--quiet', '-r',
                        'requirements.txt')
if ($LASTEXITCODE -ne 0) { Pop-Location; Fail 'The dependencies could not be installed.' }
Pop-Location

# --- 4. Two terminals ----------------------------------------------------

Step 'MetaTrader 5'

function Install-Terminal {
    param([string] $Zip, [string] $Destination)
    if (Test-Path (Join-Path $Destination 'terminal64.exe')) {
        Say ($Destination + ' already has a terminal - left alone.')
        return
    }
    Say ('Unpacking ' + $Destination + ' ...')
    New-Item -ItemType Directory -Force -Path $Destination | Out-Null
    Expand-Archive -Path $Zip -DestinationPath $Destination -Force
    if (-not (Test-Path (Join-Path $Destination 'terminal64.exe'))) {
        # A zip made of the FOLDER rather than its contents lands one
        # level deep. Flatten it, because every path in config.json is
        # about to be written assuming terminal64.exe is right here.
        $inner = Get-ChildItem -Directory $Destination |
                 Where-Object { Test-Path (Join-Path $_.FullName 'terminal64.exe') } |
                 Select-Object -First 1
        if ($inner) {
            Get-ChildItem -Force $inner.FullName |
                Move-Item -Destination $Destination -Force
            Remove-Item -Recurse -Force $inner.FullName
        }
    }
    if (-not (Test-Path (Join-Path $Destination 'terminal64.exe'))) {
        Fail ('No terminal64.exe under ' + $Destination + ' after ' +
              'unpacking. The golden zip is not a MetaTrader 5 folder.')
    }
}

Install-Terminal -Zip $GoldenZip -Destination $TerminalA
Install-Terminal -Zip $GoldenZip -Destination $TerminalB

# --- 5. The safety tests -------------------------------------------------
#
#     Before the trader is asked for a password, and before anything is
#     put on the Desktop. A broken install must fail HERE, in front of
#     whoever is installing it.

Step 'Safety tests'
Push-Location $Root
Invoke-Python $python @('-m', 'pytest', 'tests', '-q')
$testsFailed = ($LASTEXITCODE -ne 0)
Pop-Location
if ($testsFailed) {
    Fail ('The test suite failed on this machine. Nothing has been ' +
          'configured and no shortcut was made. Do not trade on this ' +
          'build - send the lines above to whoever maintains it.')
}

# --- 6. The six questions ------------------------------------------------

Step 'Accounts'
if ($KeepConfig -and (Test-Path (Join-Path $Root 'config.json'))) {
    Say 'Keeping the config already on this machine, as asked.'
} else {
    Push-Location $Root
    Invoke-Python $python @('deploy\configure.py',
                            '--root', $Root,
                            '--terminal-a', (Join-Path $TerminalA 'terminal64.exe'),
                            '--terminal-b', (Join-Path $TerminalB 'terminal64.exe'))
    if ($LASTEXITCODE -ne 0) {
        Pop-Location
        Warn ('The accounts were not saved. Everything else is installed - ' +
              'run deploy\configure.py again, or enter them on the ' +
              'Exchanges page after starting.')
    } else {
        Pop-Location
    }
}

# --- 7. Prove it -------------------------------------------------------
#
#     The install is not finished when the files are in place. It is
#     finished when this machine has actually logged both accounts in.
#
#     Doing it HERE means a wrong password, a typo'd login or a server
#     name that does not resolve is found in front of whoever is
#     installing, with the broker's own words on screen. The alternative
#     is a trader discovering it at 9am with nobody around.
#
#     It also opens both terminals, which is what the Algo Trading step
#     below needs - so the two are done in one visit rather than two.

Step 'Proving both accounts connect'
$verified = $false
if (Test-Path (Join-Path $Root 'config.json')) {
    Push-Location $Root
    Invoke-Python $python @('deploy\check_config.py', '--config',
                            'config.json')
    $verified = ($LASTEXITCODE -eq 0)
    Pop-Location
    if (-not $verified) {
        Warn ('At least one account did not connect - the reason is above, ' +
              'in the broker''s own words. Everything is installed; fix the ' +
              'account on the Exchanges page after starting, or re-run ' +
              'deploy\configure.py. Do not hand this machine over until ' +
              'both legs connect.')
    }
} else {
    Warn 'No config.json, so there is nothing to prove yet.'
}

# --- 8. The shortcut -----------------------------------------------------

Step 'Desktop shortcut'
$desktop = [Environment]::GetFolderPath('CommonDesktopDirectory')
$link = (New-Object -ComObject WScript.Shell).CreateShortcut(
    (Join-Path $desktop 'START TRADING.lnk'))
$link.TargetPath = Join-Path $Root 'START-TRADING.bat'
$link.WorkingDirectory = $Root
$link.Description = 'Start MT5-Trader and open the ladders'
$link.IconLocation = (Join-Path $TerminalA 'terminal64.exe') + ',0'
$link.Save()
Say 'START TRADING is on the Desktop.'

Write-Host ''
if ($verified) {
    Write-Host '  Done - and both accounts logged in from this machine.' `
               -ForegroundColor Green
} else {
    Write-Host '  Installed, but NOT proven - see the account errors above.' `
               -ForegroundColor Yellow
}

<#
    The one manual step in the whole rollout, and it gets its own box
    because it is the one that produces a silent failure. Algo Trading
    is a per-INSTALLATION setting living in AppData, so it cannot ride
    in the golden zip; a fresh terminal has it off, and the symptom is
    a ladder that shows prices and refuses every order.

    Step 7 left both terminals open, so this is two clicks now rather
    than a second visit.
#>
Write-Host ''
Write-Host '  ###########################################################'
Write-Host '  #  ONE THING LEFT, BY HAND, ONCE ON THIS PC:              #'
Write-Host '  #                                                         #'
Write-Host '  #  In BOTH MetaTrader 5 windows, press ALGO TRADING so    #'
Write-Host '  #  the button turns GREEN.                                #'
Write-Host '  #                                                         #'
Write-Host '  #  It is stored per installation, so it cannot be shipped #'
Write-Host '  #  in the template. Left off, the ladder shows prices and #'
Write-Host '  #  refuses every order.                                   #'
Write-Host '  ###########################################################'
Write-Host ''
Write-Host '  Do NOT run the terminals as Administrator. A terminal started'
Write-Host '  elevated will not accept a connection from a normally-started'
Write-Host '  Python, and the leg reads as unknown with no obvious reason.'
Write-Host ''
Write-Host '  Then: double-click START TRADING on the Desktop.'
Write-Host '  Any time later, deploy\VERIFY.bat re-checks this machine.'
Write-Host ''
if (-not $verified) { exit 1 }
exit 0
