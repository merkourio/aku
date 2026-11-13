# Security

Please report vulnerabilities privately through this repository's **Security →
Report a vulnerability** tab, not in a public issue. Include affected versions,
reproduction steps and impact, using synthetic audio/data only. Fixes, if any,
are made for the latest release; there is no guaranteed response time.

## Trust boundary

Aku is a single-user desktop application. Its API and whisper-server bind to
127.0.0.1, not the network. Host/origin checks restrict browser requests, but
**loopback is not authentication**: other local processes/users can call the API.
Do not expose it through a proxy, forward its ports, or run it on an untrusted
multi-user machine. AKU_DEV_ORIGIN is for local development only.

Recordings, transcripts, voice samples/embeddings, speaker names, vocabulary and
logs may be sensitive. Do not attach them to issues. API keys belong in the
system keyring/environment, never in Git or diagnostic screenshots. Data is
not encrypted by Aku; use appropriate account permissions and disk encryption.
See [privacy](docs/privacy.md).

Release checksums detect corrupt/mismatched downloads; they do not protect
against compromise of the GitHub account distributing both files and checksums.
