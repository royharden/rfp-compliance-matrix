param(
    [Parameter(Mandatory=$true)][string]$ImageId,
    [Parameter(Mandatory=$true)][string]$PortsScript,
    [Parameter(Mandatory=$true)][string]$Owner,
    [Parameter(Mandatory=$true)][string]$Instance,
    [Parameter(Mandatory=$true)][string]$Receipt
)
$ErrorActionPreference = 'Stop'
# Operator/INT must first lift any active resource hold. No implicit build,
# image pull, resource removal, credential inspection or arbitrary Docker target.
if ($ImageId -notmatch '^sha256:[a-f0-9]{64}$') { throw 'Use the exact locally built image ID' }
if ($Instance -notmatch '^r03-rv2-[a-z0-9-]+$') { throw 'Use a new r03-rv2-* instance' }
if (Test-Path -LiteralPath $Receipt) { throw 'Preserve existing receipt; choose a new path' }
& python $PortsScript claim --project m3 --instance $Instance --kind test --mode fixed --port db=21820 --owner $Owner
if ($LASTEXITCODE -ne 0) { throw 'M3 claim failed; no Docker resource created' }
& python $PortsScript check "m3/$Instance"
if ($LASTEXITCODE -ne 0) { throw 'M3 check blocked or unknown; no Docker resource created' }
$container = "m3-$Instance-db"
$volume = "m3-$Instance-state"
$labels = @('--label', 'agentports.project=m3', '--label', "agentports.instance=$Instance", '--label', "agentports.owner=$Owner")
& docker volume create @labels $volume
if ($LASTEXITCODE -ne 0) { throw 'Volume creation failed' }
# SQLite requires no host listener. The port claim is reserved but never published.
# All state survives on the labelled volume, including failed attempts.
$result = & docker run --name $container @labels --network none --read-only --tmpfs /tmp:rw,noexec,nosuid,size=67108864 --mount "type=volume,source=$volume,target=/m3-state" $ImageId --root /m3-state/m3-journey
$code = $LASTEXITCODE
$result | Set-Content -LiteralPath $Receipt -Encoding utf8
if ($code -ne 0) { throw "Container e2e failed (exit $code); preserve $container and $volume" }
& python $PortsScript observe "m3/$Instance"
if ($LASTEXITCODE -ne 0) { throw 'E2e exited zero but ledger observation failed' }
Write-Output "Preserved stopped container $container, volume $volume, receipt $Receipt"
