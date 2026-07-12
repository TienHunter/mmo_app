param([string]$Python = "")
$ErrorActionPreference = "Stop"
if (-not $Python) {
    $candidates = @(
        ".\.venv\Scripts\python.exe",
        "..\youtube_tracker\.venv\Scripts\python.exe"
    )
    $Python = $candidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
}
if (-not $Python -or -not (Test-Path -LiteralPath $Python)) {
    throw "Không tìm thấy Python build runtime. Hãy truyền -Python <đường-dẫn-python.exe>."
}
$sharedPackages = Resolve-Path "..\tiktok-auto-uploader\.venv\Lib\site-packages" -ErrorAction SilentlyContinue
if ($sharedPackages) {
    $env:PYTHONPATH = $sharedPackages.Path + [IO.Path]::PathSeparator + $env:PYTHONPATH
}
& $Python -m pip install -r requirements.txt -r requirements-build.txt
& $Python -m PyInstaller --noconfirm --clean --distpath dist mmo_app.spec
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
$zip = Join-Path (Resolve-Path .\dist) "MMOApp-Windows-x64.zip"
if (Test-Path $zip) { Remove-Item -LiteralPath $zip -Force }
tar.exe -a -c -f $zip -C .\dist MMOApp
Get-Item $zip | Select-Object FullName,Length,LastWriteTime
Get-FileHash -Algorithm SHA256 $zip | Select-Object Hash,Path
