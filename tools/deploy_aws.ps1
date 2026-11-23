param(
    [string]$Profile = 'prokjs',
    [switch]$Create,
    [switch]$UploadOnly
)
$ErrorActionPreference = 'Stop'
$root = Split-Path $PSScriptRoot -Parent
$region = 'us-east-2'
$aws = (Get-Command aws -ErrorAction SilentlyContinue).Source
if (!$aws) { $aws = "$env:LOCALAPPDATA\Programs\Amazon\AWSCLIV2\aws.exe" }
$env:Path = "$env:USERPROFILE\.cargo\bin;$env:Path"
$statePath = Join-Path $root 'aws-hosting.json'
$temp = Join-Path ([IO.Path]::GetTempPath()) ([guid]::NewGuid().ToString())
New-Item -ItemType Directory $temp | Out-Null
function Invoke-Aws([string[]]$Arguments, [string]$ApiRegion = $region) {
    $raw = & $aws @Arguments --profile $Profile --region $ApiRegion --output json --no-cli-pager
    if ($LASTEXITCODE -ne 0) { throw "AWS command failed: $($Arguments -join ' ')" }
    if ($raw) { ($raw -join "`n") | ConvertFrom-Json }
}
function Json-File($Value) {
    $path = Join-Path $temp "$([guid]::NewGuid()).json"
    [IO.File]::WriteAllText($path, ($Value | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
    return "file://$path"
}
function Save-State {
    [IO.File]::WriteAllText($statePath, ($script:state | ConvertTo-Json -Depth 30), [Text.UTF8Encoding]::new($false))
}
try {
    $identity = Invoke-Aws @('sts', 'get-caller-identity')
    if ((& $aws configure get region --profile $Profile) -ne $region) { throw "Profile must select $region; no resources changed." }
    if (Test-Path $statePath) {
        $state = Get-Content $statePath -Raw | ConvertFrom-Json
        if ($state.Account -ne $identity.Account -or $state.Region -ne $region) { throw 'Hosting state belongs to a different project or Region.' }
    } else {
        if (!$Create) { throw 'No deployment exists. Use -Create explicitly for first deployment.' }
        $state = [pscustomobject]@{ Account = $identity.Account; Region = $region; Bucket = $null; LogBucket = $null; Oac = $null; CachePolicy = $null; HeadersPolicy = $null; Distribution = $null; Domain = $null; Delivery = $null; Files = [pscustomobject]@{} }
        Save-State
    }
    Set-Location $root
    & npm.cmd run build:web
    if ($LASTEXITCODE -ne 0) { throw 'Browser build failed; nothing uploaded.' }
    $name = "prokjs-tft19-$($state.Account)"
    foreach ($property in @('Bucket', 'LogBucket')) {
        if (!$state.$property) {
            if (!$Create) { throw 'Initial hosting setup is incomplete. Resume with -Create.' }
            $bucket = if ($property -eq 'Bucket') { "$name-site" } else { "$name-logs" }
            Invoke-Aws @('s3api', 'create-bucket', '--bucket', $bucket, '--create-bucket-configuration', "LocationConstraint=$region", '--object-ownership', 'BucketOwnerEnforced') | Out-Null
            $state.$property = $bucket; Save-State
        }
        $bucket = $state.$property
        Invoke-Aws @('s3api', 'put-public-access-block', '--bucket', $bucket, '--public-access-block-configuration', 'BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true') | Out-Null
        $encryption = @{ Rules = @(@{ ApplyServerSideEncryptionByDefault = @{ SSEAlgorithm = 'AES256' } }) }
        Invoke-Aws @('s3api', 'put-bucket-encryption', '--bucket', $bucket, '--server-side-encryption-configuration', (Json-File $encryption)) | Out-Null
    }
    $retention = @{ Rules = @(@{ ID = 'expire-access-logs'; Status = 'Enabled'; Filter = @{ Prefix = '' }; Expiration = @{ Days = 30 }; AbortIncompleteMultipartUpload = @{ DaysAfterInitiation = 7 } }) }
    Invoke-Aws @('s3api', 'put-bucket-lifecycle-configuration', '--bucket', $state.LogBucket, '--lifecycle-configuration', (Json-File $retention)) | Out-Null
    if (!$UploadOnly) {
    if (!$state.Oac) {
        $oac = @{ Name = $name; OriginAccessControlOriginType = 's3'; SigningBehavior = 'always'; SigningProtocol = 'sigv4' }
        $state.Oac = (Invoke-Aws @('cloudfront', 'create-origin-access-control', '--origin-access-control-config', (Json-File $oac)) 'us-east-1').OriginAccessControl.Id; Save-State
    }
    if (!$state.CachePolicy) {
        $cache = @{ Name = $name; MinTTL = 0; DefaultTTL = 60; MaxTTL = 86400; ParametersInCacheKeyAndForwardedToOrigin = @{ EnableAcceptEncodingGzip = $true; EnableAcceptEncodingBrotli = $true; HeadersConfig = @{ HeaderBehavior = 'none' }; CookiesConfig = @{ CookieBehavior = 'none' }; QueryStringsConfig = @{ QueryStringBehavior = 'none' } } }
        $state.CachePolicy = (Invoke-Aws @('cloudfront', 'create-cache-policy', '--cache-policy-config', (Json-File $cache)) 'us-east-1').CachePolicy.Id; Save-State
    }
    if (!$state.HeadersPolicy) {
        $headers = @{ Name = $name; SecurityHeadersConfig = @{
            ContentTypeOptions = @{ Override = $true }
            FrameOptions = @{ FrameOption = 'DENY'; Override = $true }
            ReferrerPolicy = @{ ReferrerPolicy = 'no-referrer'; Override = $true }
            StrictTransportSecurity = @{ AccessControlMaxAgeSec = 31536000; IncludeSubdomains = $false; Preload = $false; Override = $true }
            ContentSecurityPolicy = @{ ContentSecurityPolicy = "default-src 'self'; script-src 'self' 'wasm-unsafe-eval'; worker-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; object-src 'none'; base-uri 'self'; form-action 'none'"; Override = $true }
        } }
        $state.HeadersPolicy = (Invoke-Aws @('cloudfront', 'create-response-headers-policy', '--response-headers-policy-config', (Json-File $headers)) 'us-east-1').ResponseHeadersPolicy.Id; Save-State
    }
    if (!$state.Distribution) {
        $config = @{
            CallerReference = $name; Comment = "prokjs Set 19 browser-local calculator"; Enabled = $true; DefaultRootObject = 'index.html'; PriceClass = 'PriceClass_100'; HttpVersion = 'http2and3'; IsIPV6Enabled = $true
            Origins = @{ Quantity = 1; Items = @(@{ Id = 'private-s3'; DomainName = "$($state.Bucket).s3.$region.amazonaws.com"; OriginAccessControlId = $state.Oac; S3OriginConfig = @{ OriginAccessIdentity = '' } }) }
            DefaultCacheBehavior = @{ TargetOriginId = 'private-s3'; ViewerProtocolPolicy = 'redirect-to-https'; Compress = $true; CachePolicyId = $state.CachePolicy; ResponseHeadersPolicyId = $state.HeadersPolicy; TrustedSigners = @{ Enabled = $false; Quantity = 0 }; AllowedMethods = @{ Quantity = 2; Items = @('GET', 'HEAD'); CachedMethods = @{ Quantity = 2; Items = @('GET', 'HEAD') } } }
            ViewerCertificate = @{ CloudFrontDefaultCertificate = $true }
            Restrictions = @{ GeoRestriction = @{ RestrictionType = 'none'; Quantity = 0 } }
        }
        $distribution = (Invoke-Aws @('cloudfront', 'create-distribution', '--distribution-config', (Json-File $config)) 'us-east-1').Distribution
        $state.Distribution = $distribution.Id; $state.Domain = $distribution.DomainName; Save-State
    }
    $distributionArn = "arn:aws:cloudfront::$($state.Account):distribution/$($state.Distribution)"
    $policy = @{ Version = '2012-10-17'; Statement = @(
        @{ Sid = 'CloudFrontReadOnly'; Effect = 'Allow'; Principal = @{ Service = 'cloudfront.amazonaws.com' }; Action = 's3:GetObject'; Resource = "arn:aws:s3:::$($state.Bucket)/*"; Condition = @{ StringEquals = @{ 'AWS:SourceArn' = $distributionArn; 'AWS:SourceAccount' = $state.Account } } },
        @{ Sid = 'HTTPSOnly'; Effect = 'Deny'; Principal = '*'; Action = 's3:*'; Resource = @("arn:aws:s3:::$($state.Bucket)", "arn:aws:s3:::$($state.Bucket)/*"); Condition = @{ Bool = @{ 'aws:SecureTransport' = 'false' } } }
    ) }
    Invoke-Aws @('s3api', 'put-bucket-policy', '--bucket', $state.Bucket, '--policy', (Json-File $policy)) | Out-Null
    if (!$state.Delivery) {
        Invoke-Aws @('logs', 'put-delivery-source', '--name', $name, '--resource-arn', $distributionArn, '--log-type', 'ACCESS_LOGS') 'us-east-1' | Out-Null
        $destination = Invoke-Aws @('logs', 'put-delivery-destination', '--name', $name, '--delivery-destination-configuration', "destinationResourceArn=arn:aws:s3:::$($state.LogBucket)", '--output-format', 'json') 'us-east-1'
        $state.Delivery = (Invoke-Aws @('logs', 'create-delivery', '--delivery-source-name', $name, '--delivery-destination-arn', $destination.deliveryDestination.arn, '--record-fields', 'date', 'time', 'cs-method', 'cs-uri-stem', 'sc-status', 'x-edge-result-type', 'time-taken', 'ssl-protocol') 'us-east-1').id; Save-State
    }
    }
    $staging = Join-Path $temp 'upload'
    New-Item -ItemType Directory $staging | Out-Null
    $frontend = Join-Path $root 'frontend-dist'
    $hashes = [ordered]@{}
    foreach ($file in Get-ChildItem $frontend -File -Recurse) {
        $relative = $file.FullName.Substring($frontend.Length + 1).Replace('\', '/')
        $hashes[$relative] = (Get-FileHash $file.FullName -Algorithm SHA256).Hash
        if ($state.Files.$relative -ne $hashes[$relative]) {
            $destination = Join-Path $staging $relative
            New-Item -ItemType Directory (Split-Path $destination -Parent) -Force | Out-Null
            Copy-Item $file.FullName $destination
        }
    }
    $landing = '<!doctype html><html lang="en"><head><meta charset="utf-8"><meta http-equiv="refresh" content="0;url=web/index.html"><title>prokjs Set 19 calculator</title></head><body><a href="web/index.html">Open the calculator</a></body></html>'
    $landingPath = Join-Path $temp 'index.html'
    [IO.File]::WriteAllText($landingPath, $landing, [Text.UTF8Encoding]::new($false))
    $hashes['index.html'] = (Get-FileHash $landingPath -Algorithm SHA256).Hash
    if ($state.Files.'index.html' -ne $hashes['index.html']) { Copy-Item $landingPath (Join-Path $staging 'index.html') }
    $changed = @(Get-ChildItem $staging -File -Recurse)
    if ($changed.Count -gt 0) {
        & $aws s3 sync $staging "s3://$($state.Bucket)/" --exclude '*.wasm' --cache-control 'public,max-age=60,must-revalidate' --sse AES256 --only-show-errors --profile $Profile --region $region --no-cli-pager
        if ($LASTEXITCODE -ne 0) { throw 'Upload failed; deployment manifest was not advanced.' }
        foreach ($file in $changed | Where-Object Extension -eq '.wasm') {
            $relative = $file.FullName.Substring($staging.Length + 1).Replace('\', '/')
            & $aws s3 cp $file.FullName "s3://$($state.Bucket)/$relative" --content-type application/wasm --cache-control 'public,max-age=60,must-revalidate' --sse AES256 --only-show-errors --profile $Profile --region $region --no-cli-pager
            if ($LASTEXITCODE -ne 0) { throw 'WASM upload failed.' }
        }
        if (!$UploadOnly) {
            Invoke-Aws @('cloudfront', 'create-invalidation', '--distribution-id', $state.Distribution, '--paths', '/*') 'us-east-1' | Out-Null
        }
    }
    $state.Files = [pscustomobject]$hashes; Save-State
    if ($UploadOnly) {
        Write-Host "Uploaded $($changed.Count) changed files to private S3 storage. This does not publish a website."
        return
    }
    Write-Host "Uploaded $($changed.Count) changed files. Waiting for CloudFront deployment..."
    & $aws cloudfront wait distribution-deployed --id $state.Distribution --profile $Profile --region us-east-1
    if ($LASTEXITCODE -ne 0) { throw 'CloudFront has not reached Deployed; check its status before announcing the site.' }
    Write-Host "Website: https://$($state.Domain)/"
    Write-Host "Console: https://us-east-1.console.aws.amazon.com/cloudfront/v4/home?region=us-east-1#/distributions/$($state.Distribution)"
} finally {
    Remove-Item $temp -Recurse -Force
}
