param(
    [switch]$DryRun,
    [switch]$ReuseExisting
)

$batchArguments = @(
    "--questions", "D:\测试记录\两轮测试问题题目汇总\01_首轮问题题_Q07_Q08_Q14\问答测试题集_第3轮.md",
    "--files-dir", "D:\测试记录\原文\首轮（Q论）问答原文",
    "--project-id", "1"
)
if ($DryRun) { $batchArguments += "--dry-run" }
if ($ReuseExisting) { $batchArguments += "--reuse-existing" }

& "$PSScriptRoot\..\agent\Scripts\python.exe" -X utf8 -B "$PSScriptRoot\batch_qa.py" @batchArguments
exit $LASTEXITCODE
