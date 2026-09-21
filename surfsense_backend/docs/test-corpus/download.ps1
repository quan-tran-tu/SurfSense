# Download Vietnamese legal documents related to cybersecurity / data
# from congbao.chinhphu.vn and vanban.chinhphu.vn.
#
# Usage:
#   .\download.ps1
#   .\download.ps1 "C:\path\to\output"
#
# Default output: .\files

param(
    [string]$Out = ""
)

$ErrorActionPreference = "Continue"

# Resolve output directory relative to this script
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path

if ([string]::IsNullOrWhiteSpace($Out)) {
    $Out = Join-Path $ScriptDir "files"
}

New-Item -ItemType Directory -Force -Path $Out | Out-Null

$UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"

# name | page containing download link
$Docs = @"
01-luat-an-ninh-mang-24-2018-qh14|https://congbao.chinhphu.vn/van-ban/luat-so-24-2018-qh14-26894.htm
02-luat-an-ninh-mang-116-2025-qh15|https://congbao.chinhphu.vn/van-ban/luat-so-116-2025-qh15-468678.htm
03-luat-an-toan-thong-tin-mang-86-2015-qh13|https://congbao.chinhphu.vn/van-ban/luat-so-86-2015-qh13-18386.htm
04-luat-du-lieu-60-2024-qh15|https://congbao.chinhphu.vn/van-ban/luat-so-60-2024-qh15-43563.htm
05-luat-bao-ve-du-lieu-ca-nhan-91-2025-qh15|https://congbao.chinhphu.vn/van-ban/luat-so-91-2025-qh15-45578.htm
06-luat-giao-dich-dien-tu-20-2023-qh15|https://congbao.chinhphu.vn/van-ban/luat-so-20-2023-qh15-39848.htm
07-nd-53-2022-chi-tiet-luat-an-ninh-mang|https://vanban.chinhphu.vn/?pageid=27160&docid=206381
08-nd-13-2023-bao-ve-du-lieu-ca-nhan|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-13-2023-nd-cp-39228.htm
09-nd-356-2025-chi-tiet-luat-bvdlcn|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-356-2025-nd-cp-468371.htm
10-nd-330-2026-xu-phat-vphc-anm-bvdlcn|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-330-2026-nd-cp-470339.htm
11-nd-72-2013-quan-ly-internet|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-72-2013-nd-cp-2542.htm
12-nd-147-2024-quan-ly-internet|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-147-2024-nd-cp-43155.htm
13-nd-85-2016-an-toan-htt-theo-cap-do|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-85-2016-nd-cp-20423.htm
14-nd-108-2016-kinh-doanh-sp-dv-attt|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-108-2016-nd-cp-20636.htm
15-nd-58-2016-mat-ma-dan-su|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-58-2016-nd-cp-20017.htm
16-nd-179-2025-ho-tro-nguoi-lam-attt-anm|https://congbao.chinhphu.vn/van-ban/nghi-dinh-so-179-2025-nd-cp-45369.htm
17-qd-05-2017-ung-cuu-khan-cap-attt|https://congbao.chinhphu.vn/van-ban/quyet-dinh-so-05-2017-qd-ttg-22514.htm
18-tt-12-2022-huong-dan-nd-85-2016|https://congbao.chinhphu.vn/van-ban/thong-tu-so-12-2022-tt-btttt-37720.htm
"@

function Resolve-Links {
    param(
        [string]$Page
    )

    try {
        $response = Invoke-WebRequest `
            -Uri $Page `
            -UserAgent $UA `
            -MaximumRedirection 10 `
            -TimeoutSec 60 `
            -UseBasicParsing

        $t = $response.Content
    }
    catch {
        return @()
    }

    $urls = @()

    # Same patterns as the original Bash script
    $patterns = @(
        'https://congbaocdn\.chinhphu\.vn/[^"\s<>]+\.pdf',
        'https://datafiles\.chinhphu\.vn/cpp/files/vbpq/[^"\s<>]+\.pdf'
    )

    foreach ($pattern in $patterns) {
        $matches = [regex]::Matches(
            $t,
            $pattern,
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )

        foreach ($match in $matches) {
            $url = $match.Value

            if (
                $url -notmatch 'image' -and
                $url -notmatch '/Icon/'
            ) {
                if ($urls -notcontains $url) {
                    $urls += $url
                }
            }
        }

        if ($urls.Count -gt 0) {
            break
        }
    }

    # Fallback to g7 CDN
    if ($urls.Count -eq 0) {
        $matches = [regex]::Matches(
            $t,
            'https://g7\.cdnchinhphu\.vn/api/download/stream\?[^"\s<>]+',
            [System.Text.RegularExpressions.RegexOptions]::IgnoreCase
        )

        foreach ($match in $matches) {
            $url = [System.Net.WebUtility]::HtmlDecode($match.Value)

            if ($urls -notcontains $url) {
                $urls += $url
            }
        }

        # Prefer URLs ending in .pdf
        $pdfUrls = @(
            $urls | Where-Object {
                $_.ToLower().EndsWith(".pdf")
            }
        )

        if ($pdfUrls.Count -gt 0) {
            $urls = $pdfUrls
        }
        elseif ($urls.Count -gt 1) {
            $urls = @($urls[0])
        }
    }

    return $urls
}

$fail = 0

$Docs -split "`r?`n" | ForEach-Object {

    $line = $_.Trim()

    if ([string]::IsNullOrWhiteSpace($line)) {
        return
    }

    $parts = $line -split '\|', 2

    if ($parts.Count -ne 2) {
        return
    }

    $name = $parts[0]
    $page = $parts[1]

    $urls = @(Resolve-Links -Page $page)

    if ($urls.Count -eq 0) {
        Write-Host ("  FAIL {0,-46} (khong tim duoc link tai)" -f $name)
        $fail = 1
        return
    }

    $i = 0

    foreach ($url in $urls) {

        $i++

        if ($urls.Count -gt 1) {
            $fileName = "$name-p$i.pdf"
        }
        else {
            $fileName = "$name.pdf"
        }

        $outputFile = Join-Path $Out $fileName

        try {
            Invoke-WebRequest `
                -Uri $url `
                -UserAgent $UA `
                -MaximumRedirection 10 `
                -TimeoutSec 180 `
                -OutFile $outputFile `
                -UseBasicParsing

            if (Test-Path $outputFile) {
                $fileInfo = Get-Item $outputFile
                $size = $fileInfo.Length

                # Check PDF magic bytes
                $bytes = [System.IO.File]::ReadAllBytes($outputFile)

                $isPdf = (
                    $bytes.Length -ge 4 -and
                    $bytes[0] -eq 0x25 -and
                    $bytes[1] -eq 0x50 -and
                    $bytes[2] -eq 0x44 -and
                    $bytes[3] -eq 0x46
                )

                if ($isPdf -and $size -gt 20000) {
                    Write-Host ("  ok   {0,-46} {1,12} bytes" -f $fileName, $size)
                }
                else {
                    Write-Host ("  FAIL {0,-46} size={1} (khong phai PDF)" -f $fileName, $size)
                    $fail = 1
                }
            }
            else {
                Write-Host ("  FAIL {0,-46} (file khong ton tai)" -f $fileName)
                $fail = 1
            }
        }
        catch {
            Write-Host ("  FAIL {0,-46} {1}" -f $fileName, $_.Exception.Message)
            $fail = 1
        }
    }
}

Write-Host ""
Write-Host "Bo van ban: $Out"
Write-Host "Kiem tra file nao khong trich xuat duoc chu (can pypdf):"
Write-Host "  pip install pypdf"
Write-Host "  python `"$ScriptDir\check-text.py`" `"$Out`""

exit $fail
