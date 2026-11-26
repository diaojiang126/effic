$errList = @()
$tokens = $null
$ast = [System.Management.Automation.Language.Parser]::ParseFile('c:\web\effic\deploy_iis.ps1', [ref]$tokens, [ref]$errList)
if ($errList.Count -eq 0) {
    Write-Host 'NO ERRORS'
} else {
    foreach ($e in $errList) {
        Write-Host ('Line ' + $e.Extent.StartLineNumber + ': ' + $e.Message)
    }
}
