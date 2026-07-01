# Repository Notes

## Git On Windows

This checkout can be owned by a different Windows identity than the Codex
process. When running Git commands in this repository, avoid the recurring
`detected dubious ownership` failure by using a per-command safe-directory
override:

```powershell
git -c safe.directory=C:/Users/troym/Git/PromptCompression status --short
```

Prefer this scoped override over changing global Git config.
