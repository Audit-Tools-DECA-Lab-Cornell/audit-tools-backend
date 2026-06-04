#!/bin/sh
if grep -q "caliber" .git/hooks/pre-commit 2>/dev/null; then
  exit 0
fi
FLAG="/tmp/caliber-nudge-$(echo "$PWD" | (shasum 2>/dev/null || sha1sum 2>/dev/null || md5sum 2>/dev/null || cksum) | cut -c1-8)"
find /tmp -maxdepth 1 -name "caliber-nudge-*" -mmin +120 -delete 2>/dev/null
if [ -f "$FLAG" ]; then
  exit 0
fi
touch "$FLAG"
printf '{"systemMessage":"Caliber agent config sync is not set up on this machine. Run /setup-caliber (~30s) to enable automatic agent-config syncing."}'
