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

---

# Windows signing (SmartScreen)

Today's Windows `setup.exe` and the app `.exe` are **unsigned**, so the first run
shows Microsoft Defender **SmartScreen** ("Windows protected your PC"). Users click
**More info → Run anyway** once — the same idea as the macOS quarantine step, and
documented in the README. The install itself needs **no admin rights** (it's a
per-user install), so this works on locked-down lab machines.

To make SmartScreen go away, the exe + installer need an **Authenticode** signature.
Since June 2023 the CA/Browser Forum requires code-signing private keys to live on
FIPS-140 hardware (an HSM/USB token or a cloud HSM) — so a plain `.pfx`-on-disk
certificate no longer works in CI. The simplest, cheapest path today is:

## Azure Trusted Signing (~$10/month)

Microsoft-run cloud signing that plugs straight into the release workflow via
`azure/trusted-signing-action`. The `build-release` workflow auto-detects the
secrets below and signs both the app exe and the installer; with no secrets it
just ships unsigned. Nothing else changes.

> Eligibility: your Azure account must pass a one-time **identity validation**
> (individual or organization). Organizations younger than 3 years can still use
> it; the "certificate subject" just shows the validated identity.

### One-time setup

1. **Azure subscription** — https://portal.azure.com (any pay-as-you-go works).
2. **Create a Trusted Signing account** (search "Trusted Signing" in the portal),
   pick a region — its endpoint is e.g. `https://wus2.codesigning.azure.net`.
3. **Complete Identity Validation**, then **create a Certificate Profile**
   (type: *Public Trust*). Note the **account name** and **profile name**.
4. **Create an app registration** (Microsoft Entra ID → App registrations → New) and
   a **client secret**. Note its **Tenant ID**, **Client ID**, **Client secret**.
5. **Grant it signing rights**: on the Trusted Signing account → Access control
   (IAM) → add role **"Trusted Signing Certificate Profile Signer"** to that app.

### Add these repo secrets

`Settings → Secrets and variables → Actions → New repository secret`:

| Secret | Value |
|--------|-------|
| `AZURE_TENANT_ID` | the app registration's Directory (tenant) ID |
| `AZURE_CLIENT_ID` | the app registration's Application (client) ID |
| `AZURE_CLIENT_SECRET` | the client secret value |
| `TRUSTED_SIGNING_ENDPOINT` | e.g. `https://wus2.codesigning.azure.net` |
| `TRUSTED_SIGNING_ACCOUNT` | the Trusted Signing account name |
| `TRUSTED_SIGNING_CERT_PROFILE` | the certificate profile name |

The next release's Windows job signs the exe and installer automatically, and
SmartScreen stops warning (reputation builds within a few downloads). Until then,
the unsigned installer is perfectly usable via **More info → Run anyway**.
