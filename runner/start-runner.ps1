<#
.SYNOPSIS
One-click start of this repository's GitHub Actions release runner in Docker Desktop.

.DESCRIPTION
Double-click Start-Release-Runner.cmd in the repository root. The runner registers to the repository
named by `git remote get-url origin` with label soims-release, uses the host Docker engine and restarts
with Docker Desktop. After that, every `git push` to a branch in release.yaml builds, publishes to Harbor
and deploys automatically. Rerunning is safe: an existing registration and container are reused.
Run it only on a PC you control, for repositories whose pushers you trust.
#>
param(
    [string]$Kubeconfig,
    [string]$Context = 'cn01-linux',
    [string]$Owner = $env:USERNAME,
    [string]$Domain = 'GAR',
    [string]$BaseImage = 'amr-registry.caas.intel.com/soia/icmpcs-soims:base',
    [switch]$Recreate,
    [switch]$Stop
)
$ErrorActionPreference = 'Stop'
$ProgressPreference = 'SilentlyContinue'
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12
Add-Type -AssemblyName System.Windows.Forms

$runnerVersion = '2.338.2'
$downloads = @{
    'actions-runner.tar.gz' = @("https://github.com/actions/runner/releases/download/v$runnerVersion/actions-runner-linux-x64-$runnerVersion.tar.gz",
                                'baebf204307bac6193480852732f0aba2ae97b6c5597c244f4d5558390147c40')
    'kubectl'               = @('https://dl.k8s.io/release/v1.26.15/bin/linux/amd64/kubectl',
                                'b75f359e6fad3cdbf05a0ee9d5872c43383683bb8527a9e078bb5b8a44350a41')
}
$image = 'vg2c-release-runner:' + $runnerVersion
$store = Join-Path $HOME '.vg2c-runner'
$storedKubeconfig = Join-Path $store 'rancher.kubeconfig'

function Show-Message([string]$Text, [string]$Buttons = 'OK', [string]$Icon = 'Information') {
    [System.Windows.Forms.MessageBox]::Show($Text, 'VG2C release runner', $Buttons, $Icon)
}

function Invoke-Docker {
    & docker @args
    if ($LASTEXITCODE) { throw "docker $($args[0]) failed with exit code $LASTEXITCODE" }
}

function Test-DockerLinux {
    $ErrorActionPreference = 'Continue'
    $os = docker info --format '{{.OSType}}' 2>$null
    return ($LASTEXITCODE -eq 0 -and $os -eq 'linux')
}

function Get-RunnerLog([string]$Name, [int]$Tail = 50) {
    $ErrorActionPreference = 'Continue'
    return (docker logs --tail $Tail $Name 2>&1 | Out-String)
}

function Get-RegistrationToken([string]$GitHubHost, [string]$Repository) {
    $api = if ($GitHubHost -eq 'github.com') { 'https://api.github.com' } else { "https://$GitHubHost/api/v3" }
    try {
        # Reuses the Git Credential Manager login; needs admin rights on the repository.
        $credential = "protocol=https`nhost=$GitHubHost`n`n" | git credential fill
        $password = ($credential | Where-Object { $_ -like 'password=*' }) -replace '^password=', ''
        if ($password) {
            $reply = Invoke-RestMethod -Method Post -Uri "$api/repos/$Repository/actions/runners/registration-token" `
                -Headers @{ Authorization = "Bearer $password"; Accept = 'application/vnd.github+json' }
            return $reply.token
        }
    } catch {
        Write-Warning "Automatic runner registration failed: $($_.Exception.Message)"
    }
    Start-Process "https://$GitHubHost/$Repository/settings/actions/runners/new?arch=x64&os=linux"
    $secure = Read-Host 'Paste the token shown after --token on the opened GitHub page' -AsSecureString
    return [Runtime.InteropServices.Marshal]::PtrToStringBSTR([Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure))
}

try {
    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Show-Message 'Docker Desktop is not installed. Install it, start it, then run this again.' 'OK' 'Error' | Out-Null
        exit 1
    }
    if ($Stop -and -not (Test-DockerLinux)) {
        Write-Host 'Docker Desktop is not running, so the runner is already stopped.' -ForegroundColor Green
        exit 0
    }
    while (-not (Test-DockerLinux)) {
        $answer = Show-Message ("Docker Desktop is not running (or uses Windows containers).`n`n" +
            "Start Docker Desktop, wait for 'Engine running' with Linux containers, then click Retry.") 'RetryCancel' 'Warning'
        if ($answer -ne 'Retry') { exit 1 }
    }

    $origin = git -C $PSScriptRoot remote get-url origin
    if ($LASTEXITCODE -or $origin -notmatch '^(?:https://(?:[^@/]+@)?|(?:ssh://)?git@)(?<host>[^/:]+)[/:](?<repo>[^/]+/[^/]+?)(?:\.git)?/?$') {
        Show-Message "Push this folder to GitHub first so it has an 'origin' remote, then run this again." 'OK' 'Error' | Out-Null
        exit 1
    }
    $gitHubHost, $repository = $Matches.host, $Matches.repo
    $name = 'vg2c-release-' + ($repository -replace '[^A-Za-z0-9]+', '-').ToLower()
    if ($name.Length -gt 63) { $name = $name.Substring(0, 63).TrimEnd('-') }

    $existing = docker ps -a --filter "name=^$name$" --format '{{.State}}'
    if ($Stop) {
        # Registration is kept; Start-Release-Runner.cmd resumes it. A release in progress is cancelled.
        if ($existing -eq 'running') { Invoke-Docker stop $name | Out-Null }
        Write-Host "Runner $name is stopped. Pushes will queue until you start it again." -ForegroundColor Green
        exit 0
    }
    if ($existing -and $Recreate) {
        Invoke-Docker rm -f $name | Out-Null
        $existing = $null
    }
    if ($existing) {
        if ($existing -ne 'running') { Invoke-Docker start $name | Out-Null }
        Write-Host "Runner $name is running for $repository. Pushes now release automatically." -ForegroundColor Green
        exit 0
    }

    if (-not (docker image ls -q $image)) {
        $cache = Join-Path $PSScriptRoot 'cache'
        New-Item -ItemType Directory -Force $cache | Out-Null
        foreach ($file in $downloads.Keys) {
            $path = Join-Path $cache $file
            if (-not (Test-Path $path)) {
                Write-Host "Downloading $file ..."
                Invoke-WebRequest -UseBasicParsing -Uri $downloads[$file][0] -OutFile $path
            }
            if ((Get-FileHash $path -Algorithm SHA256).Hash -ne $downloads[$file][1]) {
                Remove-Item $path
                throw "Checksum mismatch for $file; download removed"
            }
        }
        # BuildKit cannot fetch Harbor tokens through the proxy here; pull first and use the classic builder.
        Invoke-Docker pull $BaseImage
        $env:DOCKER_BUILDKIT = '0'
        Invoke-Docker build --build-arg "BASE_IMAGE=$BaseImage" -t $image $PSScriptRoot
    }

    if (-not (docker run --rm -v "${name}:/runner" $image bash -c 'test -f .runner && echo configured || true')) {
        $token = Get-RegistrationToken $gitHubHost $repository
        $token | docker run --rm -i -v "${name}:/runner" $image bash -c "read -r token; token=`$(printf '%s' `"`$token`" | tr -d '\r'); ./config.sh --unattended --replace --disableupdate --url https://$gitHubHost/$repository --name $name --labels soims-release --token `"`$token`""
        if ($LASTEXITCODE) { throw 'Runner registration failed; check the token and your repository admin rights' }
        Remove-Variable token
    }

    New-Item -ItemType Directory -Force $store | Out-Null
    if ($Kubeconfig) {
        Copy-Item -LiteralPath $Kubeconfig -Destination $storedKubeconfig -Force
    } elseif (-not (Test-Path $storedKubeconfig)) {
        Show-Message ("Select your Rancher kubeconfig for cluster '$Context'.`n`n" +
            "Rancher UI > cluster $Context > Download KubeConfig. Cancel skips deployment access.") | Out-Null
        $dialog = New-Object System.Windows.Forms.OpenFileDialog
        $dialog.Title = 'Select Rancher kubeconfig'
        $dialog.Filter = 'Kubeconfig (*.kubeconfig;*.yaml;*.yml;config)|*.kubeconfig;*.yaml;*.yml;config|All files (*.*)|*.*'
        if ($dialog.ShowDialog() -eq 'OK') { Copy-Item -LiteralPath $dialog.FileName -Destination $storedKubeconfig -Force }
    }

    $noProxy = "$gitHubHost,localhost,127.0.0.1,.intel.com"
    $arguments = @('run', '-d', '--name', $name, '--restart', 'unless-stopped',
        '-v', "${name}:/runner", '-v', '/var/run/docker.sock:/var/run/docker.sock',
        '-e', "NO_PROXY=$noProxy", '-e', "no_proxy=$noProxy",
        '-e', "RUNNER_RANCHER_CONTEXT=$Context", '-e', "RUNNER_IMSERVERLESS_DOMAIN=$Domain",
        '-e', "RUNNER_IMSERVERLESS_OWNER=$Owner")
    foreach ($proxy in 'HTTP_PROXY', 'HTTPS_PROXY') {
        $value = [Environment]::GetEnvironmentVariable($proxy)
        if ($value) { $arguments += @('-e', "$proxy=$value", '-e', "$($proxy.ToLower())=$value") }
    }
    if (Test-Path $storedKubeconfig) {
        $source = (Resolve-Path $storedKubeconfig).Path -replace '\\', '/'
        $arguments += @('--mount', "type=bind,source=$source,target=/run/runner-rancher.kubeconfig,readonly",
                        '-e', 'RUNNER_KUBECONFIG=/run/runner-rancher.kubeconfig')
    } else {
        Write-Warning 'No Rancher kubeconfig: releases need the RANCHER_KUBECONFIG GitHub Secret instead.'
    }
    Invoke-Docker @arguments $image | Out-Null

    for ($i = 0; $i -lt 30 -and (Get-RunnerLog $name) -notmatch 'Listening for Jobs'; $i++) { Start-Sleep 2 }
    Write-Host (Get-RunnerLog $name 5)
    Write-Host "Runner $name is registered to $repository and running. Pushes now release automatically." -ForegroundColor Green
} catch {
    Write-Host $_.Exception.Message -ForegroundColor Red
    exit 1
}
