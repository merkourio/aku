# Privacy and data handling

- **Local by default:** audio transcription and speaker separation run locally.
  Models/dependencies are downloaded from their publishers when needed; local
  processing does not mean installation/model downloads are offline.
- **Optional cloud:** selecting OpenAI for transcription or Improve sends the
  relevant recording audio to OpenAI. Cloud speaker separation can also send
  speaker reference clips. Review the provider's terms and your organisation's
  policy before enabling this. It can incur API charges.
- **Voice data:** remembered voices and your enrolled voice are stored locally
  as embeddings; treat them as sensitive, not anonymized data.
- **Storage:** sessions/audio and the database are under ~/.local/share/aku,
  settings under ~/.config/aku; exports go to your selected folder. Logs may
  contain paths/device names/error context. Review and redact before sharing.
- **Deletion:** deleting audio does not delete exported transcripts or backups.
  Uninstalling intentionally keeps recordings and settings. Forgetting a saved
  voice removes that saved entry, not past transcript speaker names.
- **Access:** Aku does not encrypt these files. New application processes use
  a restrictive umask; existing files are not retroactively changed. Loopback
  API access is not authenticated against other local processes/users.
- **Recording:** obtain participants' permission and follow applicable rules
  and workplace policy. Aku does not notify remote participants for you.

Do not use genuine meeting data in public bug reports or screenshots.
