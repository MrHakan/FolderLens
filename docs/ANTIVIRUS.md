# Antivirus false positives

FolderLens is a small open-source Python app packaged into a Windows
executable with [PyInstaller](https://pyinstaller.org/). PyInstaller-built
executables are **frequently flagged as false positives** by Windows Defender
and other antivirus engines — not because they contain anything malicious, but
because a lot of real malware also happens to be built with PyInstaller, so the
heuristics are trigger-happy about the packaging pattern itself.

This is a well-known problem with *every* PyInstaller app, and there is no
single switch that makes it disappear. What follows is everything this project
does to minimise it, and what you can do if your machine still quarantines the
download.

## What the build already does

Every measure below is baked into `FolderLens.spec`, `app.manifest`,
`make_version_info.py`, and the release workflow:

| Measure | Why it helps |
| --- | --- |
| **No UPX compression** (`upx=False`) | UPX-packed binaries are the single biggest false-positive trigger. FolderLens is never packed. |
| **Embedded version metadata** | An anonymous binary with no CompanyName / ProductName / FileVersion looks far more suspicious. FolderLens embeds real metadata. |
| **Application icon** | Legitimate software has an icon; icon-less binaries score worse. |
| **Proper manifest** (`asInvoker`) | FolderLens does **not** request administrator rights at launch, which avoids the most-scrutinised execution pattern. |
| **One-directory build option** | A plain folder of files trips far fewer heuristics than a self-extracting one-file executable that unpacks to a temp directory at runtime. |
| **Minimal bundle** | Unused heavy libraries (numpy, pandas, …) are excluded so nothing unexpected ends up in the binary. |

## If your antivirus still flags it

Try these in order.

### 1. Download the one-directory build instead

Each release ships two assets:

- `FolderLens.exe` — convenient single file (may be flagged more often)
- `FolderLens-<version>-win64.zip` — a folder build; **use this one if the
  single-file exe is quarantined.** Unzip it and run `FolderLens.exe` from the
  extracted folder.

The folder build behaves identically but avoids the runtime self-extraction
that heuristics dislike.

### 2. Report the false positive to Microsoft

This is the fix that actually clears the detection for everyone. It usually
takes Microsoft a day or two and they very reliably delist confirmed false
positives:

<https://www.microsoft.com/en-us/wdsi/filesubmission>

Choose *"Software developer"*, submit `FolderLens.exe`, and note that it is an
open-source PyInstaller build with published source at
<https://github.com/MrHakan/FolderLens>.

For other vendors, search "*<your antivirus> submit false positive*" — they all
have an equivalent form.

### 3. Add an exclusion (only if you trust the source)

If you built it yourself or downloaded it from the official Releases page, you
can add a Windows Defender exclusion:

**Settings → Privacy & security → Windows Security → Virus & threat protection
→ Manage settings → Exclusions → Add an exclusion.**

Only do this for a copy you obtained from a source you trust.

### 4. Verify the download

Every release asset has a SHA-256 digest shown on the GitHub Releases page and
in the build logs. You can confirm your copy matches:

```powershell
Get-FileHash .\FolderLens.exe -Algorithm SHA256
```

## The permanent fix: code signing

The only thing that removes false positives *reliably and up front* is signing
the executable with an **Authenticode code-signing certificate** from a trusted
CA (or an EV certificate, which builds SmartScreen reputation immediately).
Certificates cost money and require an identity check, so this project does not
ship one by default. If you fork FolderLens and want signed builds, add a
signing step to `release.yml` after the PyInstaller build:

```yaml
- name: Sign executable
  run: |
    & "signtool.exe" sign /fd SHA256 /tr http://timestamp.digicert.com /td SHA256 `
      /f cert.pfx /p ${{ secrets.CERT_PASSWORD }} dist\FolderLens.exe
```

with the certificate stored as an encrypted GitHub secret.

## Building it yourself

If you'd rather not download a prebuilt binary at all, building from source
produces a binary your own machine already trusts:

```bash
pip install -r requirements.txt
build.bat
```

The source is small and readable — `main.py`, `app.py`, `scanner.py`,
`analysis.py`, `file_utils.py`, `updater.py` — so you can audit exactly what
runs.
