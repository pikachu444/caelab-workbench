[CmdletBinding()]
param(
    [switch]$InstallOnly,
    [switch]$DryRun,
    [switch]$CreateShortcut,
    [switch]$ForceReinstall,
    [switch]$Console
)

$ErrorActionPreference = 'Stop'
# A caller started from PowerShell 7 can pass its module paths through cmd.exe.
Import-Module (Join-Path $PSHOME 'Modules\Microsoft.PowerShell.Utility\Microsoft.PowerShell.Utility.psd1')
$repoRoot = Split-Path -Parent $PSScriptRoot
$venv = Join-Path $repoRoot '.venv'
$venvPython = Join-Path $venv 'Scripts\python.exe'
$registryPath = Join-Path $repoRoot 'caelab-install-registry.json'
$stampPath = Join-Path $venv '.caelab_env.sha256'
$prepRoot = Join-Path (Split-Path -Parent $repoRoot) 'caelab-prep'
$projectFile = Join-Path $repoRoot 'pyproject.toml'
$prepProject = Join-Path $prepRoot 'pyproject.toml'
$hasPrep = Test-Path -LiteralPath $prepProject -PathType Leaf
$utf8 = [System.Text.UTF8Encoding]::new($false)

function Find-Uv {
    $command = Get-Command uv.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    $paths = @($command.Source)
    if ($env:USERPROFILE) { $paths += Join-Path $env:USERPROFILE '.local\bin\uv.exe' }
    if ($env:LOCALAPPDATA) { $paths += Join-Path $env:LOCALAPPDATA 'hermes\bin\uv.exe' }
    foreach ($path in $paths) {
        if ($path -and (Test-Path -LiteralPath $path -PathType Leaf)) {
            $null = & $path --version 2>$null
            if ($LASTEXITCODE -eq 0) { return $path }
        }
    }
}

function Get-UsablePython([string]$Executable, [string[]]$Arguments = @()) {
    if (!$Executable -or $Executable -match '[\\/]WindowsApps[\\/]python(?:3)?\.exe$') { return }
    $script = 'import sys; sys.exit(1) if not (3, 11) <= sys.version_info[:2] <= (3, 13) else print(sys.executable)'
    try {
        $path = & $Executable @Arguments -c $script 2>$null | Select-Object -Last 1
        if ($LASTEXITCODE -eq 0 -and $path -and (Test-Path -LiteralPath $path -PathType Leaf)) { return $path }
    } catch {
        return
    }
}

function Find-Python([string]$Uv) {
    $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
    foreach ($version in '3.13', '3.12', '3.11') {
        $python = Get-UsablePython $launcher.Source @("-$version")
        if ($python) { return $python }
    }
    foreach ($command in @(Get-Command python.exe -All -CommandType Application -ErrorAction SilentlyContinue)) {
        $python = Get-UsablePython $command.Source
        if ($python) { return $python }
    }
    if ($Uv) {
        $managed = & $Uv --no-python-downloads python find '>=3.11,<3.14' 2>$null | Select-Object -Last 1
        if ($LASTEXITCODE -eq 0) { return (Get-UsablePython $managed) }
    }
}

function Invoke-Checked([string]$Executable, [string[]]$Arguments, [string]$Failure) {
    & $Executable @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Failure (종료 코드 $LASTEXITCODE)" }
}

function Test-Environment([string]$Uv) {
    if (!(Test-Path -LiteralPath $venvPython -PathType Leaf)) { return $false }
    if (!(Get-UsablePython $venvPython)) { return $false }
    try {
        if (!$Uv) {
            $null = & $venvPython -m pip --version 2>$null
            if ($LASTEXITCODE -ne 0) { return $false }
        }
        $imports = 'import PySide6, vtk, scipy, pyvisco, pypdf, pymoo, SALib, sklearn, caelab, apps.workbench_qt'
        if ($hasPrep) { $imports += ', caelab_prep.ui.widgets' }
        $null = & $venvPython -c $imports 2>$null
        return ($LASTEXITCODE -eq 0)
    } catch {
        return $false
    }
}

function Get-TextHash([string]$Text) {
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try { return [BitConverter]::ToString($sha.ComputeHash($utf8.GetBytes($Text))).Replace('-', '') }
    finally { $sha.Dispose() }
}

function Get-EnvironmentStamp {
    $hashes = @((Get-FileHash -LiteralPath $projectFile -Algorithm SHA256).Hash)
    if ($hasPrep) { $hashes += (Get-FileHash -LiteralPath $prepProject -Algorithm SHA256).Hash }
    return (Get-TextHash ($hashes -join ':'))
}

function Find-ProgramFile([string[]]$Names, [string[]]$Patterns) {
    foreach ($name in $Names) {
        $command = Get-Command $name -CommandType Application -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($command) { return $command.Source }
    }
    foreach ($pattern in $Patterns) {
        foreach ($path in @(Resolve-Path -Path $pattern -ErrorAction SilentlyContinue)) {
            if (Test-Path -LiteralPath $path.ProviderPath -PathType Leaf) { return $path.ProviderPath }
        }
    }
}

function Find-OpenCourant([string[]]$Roots) {
    $starters = @(
        Get-Command starter_win64.exe -All -CommandType Application -ErrorAction SilentlyContinue |
            ForEach-Object Source
    )
    $launchers = @(Get-Command OpenCourant.exe -All -CommandType Application -ErrorAction SilentlyContinue)
    foreach ($launcher in $launchers) {
        $homePath = Split-Path -Parent $launcher.Source
        $starters += Join-Path $homePath 'exec\starter_win64.exe'
        $starters += Join-Path $homePath 'starter_win64.exe'
    }
    foreach ($root in $Roots) {
        foreach ($pattern in "$root\OpenCourant*\exec\starter_win64.exe", "$root\OpenRadioss*\exec\starter_win64.exe") {
            $starters += @(Resolve-Path -Path $pattern -ErrorAction SilentlyContinue | ForEach-Object ProviderPath)
        }
    }
    foreach ($starter in $starters) {
        $engine = Join-Path (Split-Path -Parent $starter) 'engine_win64.exe'
        if ((Test-Path -LiteralPath $starter -PathType Leaf) -and (Test-Path -LiteralPath $engine -PathType Leaf)) {
            return $starter
        }
    }
}

function Update-Registry {
    $exists = Test-Path -LiteralPath $registryPath -PathType Leaf
    if ($exists -and !$ForceReinstall) { return }
    if ($exists) {
        $data = ConvertFrom-Json ([IO.File]::ReadAllText($registryPath, $utf8))
        if ($data -isnot [PSCustomObject]) { throw '설치 레지스트리에 JSON 개체가 필요합니다.' }
        foreach ($key in 'env', 'runtime_env', 'programs') {
            if (!$data.PSObject.Properties[$key]) {
                $data | Add-Member NoteProperty $key ([PSCustomObject]@{})
            } elseif ($data.$key -isnot [PSCustomObject]) {
                throw "설치 레지스트리의 $key 항목에 JSON 개체가 필요합니다."
            }
        }
        if (!$data.PSObject.Properties['root']) { $data | Add-Member NoteProperty root '.' }
    } else {
        $data = [PSCustomObject]@{ root = '.'; env = [PSCustomObject]@{};
            runtime_env = [PSCustomObject]@{}; programs = [PSCustomObject]@{} }
    }

    $roots = @($env:ProgramFiles, ${env:ProgramFiles(x86)}, "$env:LOCALAPPDATA\Programs") |
        Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Container) }
    $detections = @(
        @{ Id = 'freecad'; Env = 'CAELAB_FREECAD'; Path = Find-ProgramFile @('FreeCAD.exe') @($roots | ForEach-Object { "$_\FreeCAD*\bin\FreeCAD.exe" }) }
        @{ Id = 'prepomax'; Env = 'CAELAB_PREPOMAX'; Path = Find-ProgramFile @('PrePoMax.exe') @($roots | ForEach-Object { "$_\PrePoMax*\PrePoMax.exe" }) }
        @{ Id = 'gmsh'; Env = 'CAELAB_GMSH'; Path = Find-ProgramFile @('gmsh.exe') @($roots | ForEach-Object { "$_\gmsh*\gmsh.exe" }) }
        @{ Id = 'calculix'; Env = 'CAELAB_CCX'; Path = Find-ProgramFile @('ccx.exe') @($roots | ForEach-Object { "$_\CalculiX*\ccx.exe"; "$_\CalculiX*\bin\ccx.exe" }) }
        @{ Id = 'opencourant'; Env = 'CAELAB_OPENCOURANT'; Path = Find-OpenCourant $roots }
        @{ Id = 'paraview'; Env = 'CAELAB_PARAVIEW'; Path = Find-ProgramFile @('paraview.exe') @($roots | ForEach-Object { "$_\ParaView*\bin\paraview.exe" }) }
        @{ Id = 'caelab_prep'; Env = 'CAELAB_PREP'; Path = Find-ProgramFile @('caelab-prep.exe') @("$prepRoot\.venv\Scripts\caelab-prep.exe", "$venv\Scripts\caelab-prep.exe") }
    )
    foreach ($item in $detections) {
        $entry = $data.programs.($item.Id)
        if (!$item.Path -or $data.env.PSObject.Properties[$item.Env] -or ($entry -and $entry.PSObject.Properties['path'])) { continue }
        if (!$entry) {
            $entry = [PSCustomObject]@{}
            $data.programs | Add-Member NoteProperty $item.Id $entry
        }
        if ($entry -is [PSCustomObject]) {
            $entry | Add-Member NoteProperty path $item.Path
        }
    }
    $temporary = "$registryPath.$([Guid]::NewGuid().ToString('N')).tmp"
    $previous = "$temporary.previous"
    try {
        [IO.File]::WriteAllText($temporary, ($data | ConvertTo-Json -Depth 100), $utf8)
        if ($exists) { [IO.File]::Replace($temporary, $registryPath, $previous) }
        else { [IO.File]::Move($temporary, $registryPath) }
    } finally {
        if (Test-Path -LiteralPath $temporary) { Remove-Item -LiteralPath $temporary }
        if (Test-Path -LiteralPath $previous) { Remove-Item -LiteralPath $previous }
    }
}

$mutex = $null
$locked = $false
Push-Location -LiteralPath $repoRoot
try {
    $uv = Find-Uv
    $stamp = Get-EnvironmentStamp
    if ($DryRun) {
        $basePython = Find-Python $uv
        Write-Host "점검 모드 | Workbench: $repoRoot"
        Write-Host "Python 3.11–3.13: $basePython | uv: $uv"
        Write-Host "가상환경: $venv | Prep 위젯: $hasPrep | 레지스트리: $registryPath"
        exit 0
    }

    $mutexName = 'Local\CAELabWorkbench-' + (Get-TextHash $repoRoot.ToLowerInvariant())
    $mutex = [System.Threading.Mutex]::new($false, $mutexName)
    try { $locked = $mutex.WaitOne(0) }
    catch [System.Threading.AbandonedMutexException] { $locked = $true }
    if (!$locked) { Write-Host 'CAELab Workbench 설치 또는 시작이 이미 진행 중입니다.'; exit 0 }

    $healthy = Test-Environment $uv
    $installedStamp = if (Test-Path -LiteralPath $stampPath -PathType Leaf) {
        [IO.File]::ReadAllText($stampPath).Trim()
    }
    if (!$healthy -or $installedStamp -ne $stamp -or $ForceReinstall) {
        $backup = $null
        if (!$healthy) {
            $basePython = Find-Python $uv
            if (!$basePython) {
                throw "Python 3.11–3.13 (64비트)을 설치한 뒤 다시 실행하세요. https://www.python.org/downloads/windows/"
            }
            if (Test-Path -LiteralPath $venv) {
                $backup = "$venv.caelab-recovery-$([Guid]::NewGuid().ToString('N'))"
                Move-Item -LiteralPath $venv -Destination $backup
                Write-Host "기존 가상환경을 다음 경로에 보관했습니다. $backup"
            }
        }
        try {
            if (!$healthy) {
                if ($uv) { Invoke-Checked $uv @('venv', '--seed', '--python', $basePython, $venv) '가상환경 생성 실패' }
                else { Invoke-Checked $basePython @('-m', 'venv', $venv) '가상환경 생성 실패' }
            }
            $editable = @('-e', ".[gui,rag,mdo]")
            if ($hasPrep) { $editable += @('-e', "$prepRoot[widgets]") }
            Write-Host 'CAELab Workbench 실행 환경을 설치합니다.'
            if ($uv) {
                $install = @('pip', 'install', '--python', $venvPython)
                if ($ForceReinstall) { $install += '--reinstall' }
                Invoke-Checked $uv ($install + $editable) '의존성 설치 실패'
            } else {
                $install = @('-m', 'pip', 'install')
                if ($ForceReinstall) { $install += '--force-reinstall' }
                Invoke-Checked $venvPython ($install + $editable) '의존성 설치 실패'
            }
            if (!(Test-Environment $uv)) { throw '설치한 실행 환경을 열 수 없습니다. 위 설치 결과를 확인하세요.' }
            [IO.File]::WriteAllText($stampPath, $stamp, $utf8)
        } catch {
            if ($backup) {
                if (Test-Path -LiteralPath $venv) {
                    Move-Item -LiteralPath $venv -Destination "$venv.caelab-recovery-$([Guid]::NewGuid().ToString('N'))"
                }
                Move-Item -LiteralPath $backup -Destination $venv
            }
            throw
        }
    } else {
        Write-Host '기존 CAELab Workbench 실행 환경을 사용합니다.'
    }

    Update-Registry
    if ($CreateShortcut) {
        $desktop = [Environment]::GetFolderPath('DesktopDirectory')
        if (!$desktop) { throw '사용자 바탕 화면 경로를 찾지 못했습니다.' }
        $shell = New-Object -ComObject WScript.Shell
        $shortcut = $shell.CreateShortcut((Join-Path $desktop 'CAELab Workbench.lnk'))
        $shortcut.TargetPath = Join-Path $repoRoot 'CAELab_Install_And_Run.bat'
        $shortcut.WorkingDirectory = $repoRoot
        $shortcut.Save()
    }
    if (!$InstallOnly) {
        if (Test-Path -LiteralPath $registryPath -PathType Leaf) { $env:CAELAB_INSTALL_REGISTRY = $registryPath }
        if ($Console) {
            Start-Process -FilePath $venvPython -ArgumentList '-m', 'apps.workbench_qt' -WorkingDirectory $repoRoot
        } else {
            $pythonw = Join-Path $venv 'Scripts\pythonw.exe'
            $run = [System.Diagnostics.ProcessStartInfo]::new()
            $run.FileName = if (Test-Path -LiteralPath $pythonw -PathType Leaf) { $pythonw } else { $venvPython }
            $run.Arguments = '-m apps.workbench_qt'
            $run.WorkingDirectory = $repoRoot
            $run.UseShellExecute = $false
            $run.CreateNoWindow = $true
            $process = [System.Diagnostics.Process]::Start($run)
            $process.Dispose()
        }
    }
} catch {
    [Console]::Error.WriteLine("CAELab Workbench 오류: $($_.Exception.Message)")
    exit 1
} finally {
    if ($locked) { $mutex.ReleaseMutex() }
    if ($null -ne $mutex) { $mutex.Dispose() }
    Pop-Location
}
