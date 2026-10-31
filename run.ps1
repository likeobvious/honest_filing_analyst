<#
.SYNOPSIS
    Windows PowerShell helper script for PS5 Honest Filing Analyst (equivalent to Makefile).

.EXAMPLE
    .\run.ps1 download-pdfs
    .\run.ps1 test
    .\run.ps1 download-models
#>

param (
    [Parameter(Position=0, Mandatory=$false)]
    [string]$Command = "help",

    [Parameter(Position=1, ValueFromRemainingArguments=$true)]
    [string[]]$ArgsList
)

$ErrorActionPreference = "Stop"

function Show-Help {
    Write-Host "PS5 Honest Filing Analyst - PowerShell Commands:" -ForegroundColor Cyan
    Write-Host "  .\run.ps1 download-pdfs      - Download sample 10-K PDFs into data/pdfs/"
    Write-Host "  .\run.ps1 test               - Run unit tests (tests/test_teammate1.py)"
    Write-Host "  .\run.ps1 download-models    - Download & quantize ONNX models"
    Write-Host "  .\run.ps1 up                 - Start Docker Compose stack"
    Write-Host "  .\run.ps1 down               - Stop Docker Compose stack"
    Write-Host "  .\run.ps1 logs               - View Docker container logs"
}

switch ($Command.ToLower()) {
    "download-pdfs" {
        Write-Host "Downloading FinanceBench sample PDFs..." -ForegroundColor Green
        py -3.12 scripts/download_sample_pdfs.py --limit 15 $ArgsList
    }
    "test" {
        Write-Host "Running unit test suite..." -ForegroundColor Green
        $env:PYTHONPATH = (Get-Location).Path
        py -3.12 -m unittest discover -s tests -p "test_*.py" -v
    }
    "download-models" {
        Write-Host "Downloading & quantizing ONNX models..." -ForegroundColor Green
        py -3.12 scripts/download_models.py --output-dir models $ArgsList
    }
    "up" {
        Write-Host "Starting Docker Compose services..." -ForegroundColor Green
        docker compose up --build -d
    }
    "down" {
        Write-Host "Stopping Docker Compose services..." -ForegroundColor Green
        docker compose down
    }
    "logs" {
        docker compose logs -f
    }
    default {
        Show-Help
    }
}
