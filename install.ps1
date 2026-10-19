# LocalFlow installer.
# Easiest: double-click install.bat
# Or:      powershell -ExecutionPolicy Bypass -File install.ps1 [-Startup] [-NoiseSuppression]
param([switch]$Startup, [switch]$NoiseSuppression)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
try { Start-Transcript -Path (Join-Path $PSScriptRoot "install.log") -Force | Out-Null } catch {}

function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }

function Invoke-Checked {
    # Run a native command and stop if it fails (native errors don't throw on their own).
    param([string]$Exe, [string[]]$Arguments, [string]$What)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$What failed (exit code $LASTEXITCODE). See the messages above." }
}

function Test-PythonCandidate {
    # Returns $true if this command runs a real Python 3.11-3.13.
    # (The Microsoft Store "python" alias exists but isn't a real install.)
    param([string]$Exe, [string[]]$PreArgs)
    try {
        $v = & $Exe @PreArgs -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
        if ($LASTEXITCODE -ne 0 -or -not $v) { return $false }
        $parts = "$v".Trim().Split(".")
        $major = [int]$parts[0]; $minor = [int]$parts[1]
        return ($major -eq 3 -and $minor -ge 11 -and $minor -le 13)
    } catch { return $false }
}

function Find-Python {
    if (Get-Command py -ErrorAction SilentlyContinue) {
        foreach ($v in "3.12", "3.11", "3.13") {
            if (Test-PythonCandidate "py" @("-$v")) { return @{ Exe = "py"; Args = @("-$v") } }
        }
    }
    foreach ($name in "python", "python3") {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source -notlike "*WindowsApps*" -and (Test-PythonCandidate $name @())) {
            return @{ Exe = $name; Args = @() }
        }
    }
    return $null
}

$exitCode = 1
try {
    Step "Looking for Python 3.11-3.13"
    $py = Find-Python
    if (-not $py) {
        Write-Host "Python 3.11-3.13 wasn't found." -ForegroundColor Yellow
        $ans = Read-Host "Install Python 3.12 now? (Y/n)"
        if ($ans -eq "" -or $ans -match "^[Yy]") {
            $installed = $false
            if (Get-Command winget -ErrorAction SilentlyContinue) {
                # --source winget: skip the Microsoft Store catalogue, which often fails to resolve
                & winget install -e --id Python.Python.3.12 --source winget --scope user `
                    --accept-package-agreements --accept-source-agreements
                $installed = ($LASTEXITCODE -eq 0)
                if (-not $installed) { Write-Host "winget couldn't install it - downloading from python.org instead." -ForegroundColor Yellow }
            }
            if (-not $installed) {
                $url = "https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe"
                $exe = Join-Path $env:TEMP "python-3.12.10-amd64.exe"
                Write-Host "Downloading $url"
                [Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
                $ProgressPreference = "SilentlyContinue"   # makes Invoke-WebRequest much faster
                Invoke-WebRequest -Uri $url -OutFile $exe -UseBasicParsing
                Write-Host "Installing Python 3.12 (just for your user, adds it to PATH)..."
                $p = Start-Process -FilePath $exe -Wait -PassThru -ArgumentList `
                    "/quiet", "InstallAllUsers=0", "PrependPath=1", "Include_launcher=1", "Include_test=0"
                if ($p.ExitCode -ne 0) { throw "The Python installer failed (exit code $($p.ExitCode))." }
            }
            # installers don't refresh PATH in this window, so reload it
            $env:Path = [Environment]::GetEnvironmentVariable("Path", "Machine") + ";" + [Environment]::GetEnvironmentVariable("Path", "User")
            $userPy = Join-Path $env:LOCALAPPDATA "Programs\Python\Python312"
            if (Test-Path $userPy) { $env:Path = "$userPy;$userPy\Scripts;" + $env:Path }
            $py = Find-Python
        }
        if (-not $py) {
            throw "Install Python 3.12 from https://www.python.org/downloads/ (tick 'Add python.exe to PATH'), then run install.bat again."
        }
    }
    $pyVer = & $py.Exe @($py.Args + @("--version"))
    Write-Host "Using $pyVer"

    Step "Creating virtual environment (.venv)"
    if (-not (Test-Path ".venv\Scripts\python.exe")) {
        if (Test-Path .venv) { Remove-Item .venv -Recurse -Force }
        Invoke-Checked $py.Exe ($py.Args + @("-m", "venv", ".venv")) "Creating the virtual environment"
    }
    $vpy = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"

    Step "Installing packages (first time downloads ~1.5 GB of CUDA libraries - this takes a while)"
    Invoke-Checked $vpy @("-m", "pip", "install", "--upgrade", "pip", "-q") "Upgrading pip"
    Invoke-Checked $vpy @("-m", "pip", "install", "-r", "requirements.txt") "Installing packages"
    if ($NoiseSuppression) { Invoke-Checked $vpy @("-m", "pip", "install", "noisereduce") "Installing noisereduce" }

    if (-not (Test-Path config.json)) { Copy-Item config.example.json config.json }
    $cfg = Get-Content config.json -Raw | ConvertFrom-Json

    Step "Downloading the Whisper model (one-time, ~1.6 GB)"
    Invoke-Checked $vpy @("-m", "localflow", "--download-model") "Downloading the Whisper model"

    $model = "qwen3:4b-instruct"
    if ($cfg.cleanup -and $cfg.cleanup.llm_model) { $model = $cfg.cleanup.llm_model }
    elseif ($cfg.llm_model) { $model = $cfg.llm_model }
    if (Get-Command ollama -ErrorAction SilentlyContinue) {
        Step "Pulling $model for AI cleanup"
        & ollama pull $model
        if ($LASTEXITCODE -ne 0) {
            Write-Host "Couldn't pull $model (is Ollama running?). Dictation still works; run 'ollama pull $model' later." -ForegroundColor Yellow
        }
    } else {
        Write-Host "`nOllama not found. For AI cleanup install it:  winget install Ollama.Ollama" -ForegroundColor Yellow
        Write-Host "then run:  ollama pull $model   (dictation works without it)" -ForegroundColor Yellow
    }

    if ($Startup) {
        Step "Adding LocalFlow to Windows startup"
        $lnk = Join-Path ([Environment]::GetFolderPath("Startup")) "LocalFlow.lnk"
        $s = (New-Object -ComObject WScript.Shell).CreateShortcut($lnk)
        $s.TargetPath = Join-Path $PSScriptRoot ".venv\Scripts\pythonw.exe"
        $s.Arguments = "-m localflow"
        $s.WorkingDirectory = $PSScriptRoot
        $s.Save()
    }

    Write-Host "`nDone! Quit Wispr Flow (same hotkey), then double-click run.bat." -ForegroundColor Green
    Write-Host "Double-click the tray icon for Settings." -ForegroundColor Green
    $exitCode = 0
}
catch {
    Write-Host "`nINSTALL FAILED:" -ForegroundColor Red
    Write-Host $_.Exception.Message -ForegroundColor Red
    Write-Host "`nFull log saved to install.log in this folder." -ForegroundColor Yellow
}
finally {
    try { Stop-Transcript | Out-Null } catch {}
}

Read-Host "`nPress Enter to close"
exit $exitCode
