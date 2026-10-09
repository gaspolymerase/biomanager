# Backups for BioManager: pg_dump of the database + a tarball of the app's
# data volume (uploads, signing key), checked, pruned, optionally copied
# off-site with restic, and test-restored once a week.
FROM postgres:16

RUN apt-get update \
 && apt-get install -y --no-install-recommends restic curl ca-certificates \
 && rm -rf /var/lib/apt/lists/*

COPY backup.sh restore-test.sh restore.sh start-over.sh schedule.sh healthcheck.sh /usr/local/bin/
RUN chmod 755 /usr/local/bin/*.sh

HEALTHCHECK --interval=15m --timeout=10s --start-period=30m CMD ["/usr/local/bin/healthcheck.sh"]
ENTRYPOINT ["/usr/local/bin/schedule.sh"]
