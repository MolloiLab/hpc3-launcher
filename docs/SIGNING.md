# macOS signing & notarization

Today's macOS DMG is **ad-hoc signed and not notarized**, so first launch needs the
one-time quarantine step in the README. To make the app open with **zero warnings**
(double-click, done), set up a Developer ID and add six GitHub secrets — the
`build-release` workflow detects them and switches to real signing + notarization
automatically. Nothing else changes.

## What's on this machine right now

Checked on the build Mac (`security find-identity -v`):

- Apple team **`SLMK3SDFL5`** ("Dale Black") exists.
- The only certificate present is an **Apple Development** cert for `daleb109@yahoo.com`,
  which is **expired** (Oct 2025) and has no usable key.
- **No `Developer ID Application` certificate** is installed — that's the one required
  to distribute/notarize a signed app.

So notarization is **not** possible until a Developer ID Application cert is created.

## One-time setup

1. **Apple Developer Program** — confirm an active paid membership
   (https://developer.apple.com/account). Developer ID certs require it.
2. **Create a "Developer ID Application" certificate**
   - Xcode → Settings → Accounts → (your team) → **Manage Certificates** → **+** →
     **Developer ID Application**, or
   - https://developer.apple.com/account/resources/certificates → **+** →
     *Developer ID Application*.
   It lands in your login keychain; confirm with:
   ```bash
   security find-identity -v -p codesigning
   # -> "Developer ID Application: Dale Black (SLMK3SDFL5)"
   ```
3. **Export it as a .p12** (Keychain Access → right-click the cert → Export → .p12,
   set a password), then base64-encode it:
   ```bash
   base64 -i DeveloperID.p12 | pbcopy   # now on your clipboard
   ```
4. **App-specific password** for notarization — https://appleid.apple.com → Sign-In &
   Security → App-Specific Passwords → generate one (label it "hpc3-launcher notary").

## Add these repo secrets

`Settings → Secrets and variables → Actions → New repository secret`:

| Secret | Value |
|--------|-------|
| `MACOS_DEVELOPER_ID` | `Developer ID Application: Dale Black (SLMK3SDFL5)` |
| `MACOS_CERTIFICATE_P12` | the base64 string from step 3 |
| `MACOS_CERTIFICATE_PASSWORD` | the .p12 export password |
| `MACOS_NOTARY_APPLE_ID` | your Apple ID email |
| `MACOS_NOTARY_TEAM_ID` | `SLMK3SDFL5` |
| `MACOS_NOTARY_PASSWORD` | the app-specific password from step 4 |

The next release's macOS job will Developer-ID-sign with the hardened runtime,
notarize via `notarytool`, and staple the ticket. Users then just double-click.
