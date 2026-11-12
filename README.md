# Portable VG2C Job

## Target Products

`JobRuntime.from_script(..., site="KM")` exposes the normalized site as
`job.site`. The job rereads the following allowlist before each run:

```text
\\AZatshfs.intel.com\AZatanalysis$\MAOATM\Config\VF_POR_Cfg\ICM_PCS\NGICMPCS_JDSC_BAR_SKIP\<SITE>\prod.txt
```

Use one MARS `F_Product.prodgroup3` code per line. Initial KM contents:

```text
SQH
SQL
```

`SQH` targets Square Hill; `SQL` targets Square Hill Large. Blank lines are
ignored and codes are matched case-insensitively. Missing, unreadable, or empty
files stop the run before database queries or AED calls; there is no unrestricted
fallback. Each site needs its own file.

The MARS query uses DataSyncX through `job.sql(database="MARS")` and links each
entity-history occurrence to `F_LotHist` and the latest `F_Product` metadata.
Only allowed products enter first-lot ranking: an earlier disallowed lot does not
hide a later allowed lot. Output columns and tie handling remain unchanged.

For Linux deployment, mount the shared config root read-only and set runtime
`PRODUCT_CONFIG_ROOT` to its local mount path. The mounted root must contain
`<SITE>/prod.txt`; Linux does not resolve Windows UNC paths directly.

## Standalone GitHub Deployment

This folder now includes `release.yaml`, `automation/`, `.gitignore`, and
`.github/workflows/release.yml`. Make this folder the Git repository root;
GitHub ignores workflows nested beneath a job subfolder. Push committed source
on `main`, `master`, or `prod`, or select **Actions > Build, push and deploy VG2C job > Run workflow**.
Manual deployment defaults on; uncheck deployment for image-only publication.
The translating machine and compiler installation are not required.

The included workflow pulls the SOIMS base, installs dependencies, validates
non-root startup, publishes to Harbor, and deploys an immutable digest to
IMServerless/Rancher. It checks and attaches the shared `workflow-testing-database`
(`NAME`, `PW`), `aed-api-key` (`API_KEY`, `API_CONFIG`), and `itcaas-regcred`
Secrets in `icmpcs-soims`. No user-specific credential entry is required.
Credential values stay in Kubernetes, not in this folder or image.

The standalone target is recorded in `release.yaml` and remains stable after
copying or renaming the folder. It is separate from the previously published
private SOIMS first-lot app below. `main` deploys the base test target; `master`
deploys a separate `-master` test target, and `prod` deploys a separate `-prod`
test target. The `prod` branch name still uses `ENV_MODE=test`; it does not switch
to production. Other branches must be added to the `branches` map in
`automation/settings.yaml` with unique suffixes before release. That file also
names the Kubernetes Secrets the workflow attaches; neither is sent to IMServerless.

The destination repository must be allowed to use the shared Linux
`soims-release` runner. Platform maintainers provide protected Rancher access,
context, owner and domain through the `soims-test` GitHub environment or the
runner defaults (`RUNNER_KUBECONFIG`, `RUNNER_RANCHER_CONTEXT`,
`RUNNER_IMSERVERLESS_OWNER`, `RUNNER_IMSERVERLESS_DOMAIN`). An authorized shared
runner is still necessary; source files cannot grant deployment permissions.
The workflow reuses these settings automatically, not per-user setup.

Without a shared runner, double-click `Start-Release-Runner.cmd` after the
first push. With Docker Desktop running (it prompts if not), it builds a runner
image, registers it to this repository's `origin` with label `soims-release`
using your Git login (repository admin), and keeps it running with Docker.
On first use it asks once for your Rancher kubeconfig; it is kept in
`%USERPROFILE%\.vg2c-runner`, never in the repository. Defaults: context
`cn01-linux`, domain `GAR`, owner = your Windows username. Override with
`runner\start-runner.ps1 -Owner <id> -Context <ctx> -Domain <domain> -Recreate`.
Afterwards, every `git push` releases while the PC and Docker stay on.
Double-click `Stop-Release-Runner.cmd` to stop it; pushes then queue on GitHub
until you start it again. Stopping cancels a release in progress.
The runner controls the host Docker engine: use it only for trusted repositories.

The standalone default does not create a schedule or persistent output mount.
Add these through the platform when required. Existing test-mode exclusions
are unchanged; this job keeps read-only AED enabled and email/MMS disabled.

## IMServerless UI Upload

`release.yaml` can be uploaded as-is in the IMServerless UI. Its `:latest` tag
points at the newest successfully released image of the base app; for another
branch's app, add that branch's suffix to `funcname`, `dnshostname` and the
image repository. The workflow sets `version` and `action` itself; for a UI
upload, set `version` above the current one (for example `1.4` to `1.5`) and
`action: update` once the app exists. UI uploads do not attach the Kubernetes
Secrets (`NAME`/`PW`, AED keys) or the pull Secret; the workflow patches those
only into versions it deploys itself. An uploaded version therefore runs
without them; release through the workflow when the job needs database or AED
access.

## Run Locally (One Click)

Double-click `Run-Local.cmd` to run `main.py` on this PC. The first run creates
a project `.venv` (Python 3.13, 3.12, 3.11 or 3.14 via the `py` launcher, else
`python`) and installs `dependencies/requirements-local.txt`, which adds the
DataSyncX git source (the Docker base already provides it; first install needs
Git and mfg-github access) to `requirements.txt`. Later runs start
immediately and reinstall only after either file changes. Delete `.venv` to
rebuild it. In VS Code, select `.venv` as the interpreter to run or debug
`main.py` directly. Put local `NAME`/`PW` (and any other runtime values) in a
project `.env`; it is git- and Docker-ignored. `.venv`, `Run-Local.cmd` and
`requirements-local.txt` are excluded from the Docker image.

## Local Build and Runtime

This folder is independent of the translating machine and the installed VG2C
compiler. `dependencies/vg2c/runtime` contains the runtime snapshot,
`dependencies` contains the approved AED wheel and dependency requirements,
and `main.py` contains the editable job. SQL and HTML assets stay editable.

Build from this folder:

```sh
docker build -t my-job:local .
docker run --rm --network none --entrypoint python my-job:local -c "import main; assert callable(main.handle)"
```

The default base is `amr-registry.caas.intel.com/soia/icmpcs-soims:base`.
Override it with `--build-arg SOIMS_BASE_IMAGE=<approved-image>` when needed.
The base supplies the imserverless host, DataSyncX/Oracle system dependencies
and corporate trust. Additional Python packages in `dependencies/requirements.txt`
are installed during the build. Registry and package-index access are required
for uncached builds; this is not an offline wheelhouse for every third-party library.

The hosted entry is `main.handle(job_id, context, header, body)`, matching SOIMS.
It calls `run()` and returns success only if the job completes without an exception.
The compatible shared base supplies `/etc/icmpcs/conf/func.json` configured to
load `main.py`; this project does not duplicate that config or use a wrapper file.
Mount writable persistent storage at `/app/output` for job results.

The image defaults to `ENV_MODE=test`. Approve production before setting
`ENV_MODE=prod`. Configure `SITE` and database credentials through runtime Secrets
according to your DataSyncX/base image contract. Do not add credentials to source.

For AED, inject an authorized `X_API_KEY`, the encrypted `API_KEY`/`API_CONFIG`
pair, or mount `key.key` and `config_new.json` read-only and set `AED_SECRETS_DIR`
to their absolute container directory. A direct `X_API_KEY` overrides encrypted
credentials, so remove stale keys when changing credential sources.

To test AED without running the job or manufacturing writes:

```sh
docker run --rm --entrypoint python \
  --mount type=bind,source=/private/aed-secrets,target=/run/secrets/aed,readonly \
  -e AED_SECRETS_DIR=/run/secrets/aed my-job:local \
  -c "from vg2c.runtime import AEDClient; assert AEDClient('A15').test_connection('7645D737')"
```

Use a known authorized lot for your facility. This verifies read access, not
permission to change manufacturing state. Secret files, `.env` files, local
virtual environments and outputs are excluded from the Docker context.

The image creates a read-only `/app/.env` containing only `LOG_LEVEL=INFO`.
DataSyncX hardcodes this Linux path and otherwise tries to create it as a
non-root worker. Database credentials come from runtime `NAME` and `PW`; the
JobRuntime backend passes them explicitly to its inferred MARS reader. Never put their values in
Dockerfile `ENV`, build arguments, the AppSpec, or source.
On Linux, JobRuntime temporarily exposes `PW` under the uppercase account-name
environment key required by DataSyncX's Kerberos lookup, restoring its prior value
after each read, including failures. It uses `MarsReader`, preserving the original
runtime authentication path; no credentials are written to `.env`.
The commented `job.environ["NAME"]` and `job.environ["PW"]` lines in `main.py`
allow local overrides when runtime environment injection is unavailable.
Keep filled-in overrides local; never commit real credentials.

The previously published deployment lives in the private SOIMS repository on branch
`first-lot-after-ept-lots`. Update the job folder on that branch and push to
build, validate, publish to Harbor, and deploy a new immutable image digest.
The dedicated app is `icmpcs-soims/first-lot-after-ept-lots`, displayed as
"SOIA Workflow Testing". Runtime database and AED credentials come from
Kubernetes Secrets. Its IMServerless time-based trigger uses 30-minute intervals
and the latest app version. The release image stores output under the KM share's
`first_lot_after_ept_lots` subdirectory. Changes here must be copied to the
release branch before they are deployed.

For routine GitHub releases, users need no local Docker or registry/Rancher setup.
The workflow handles approved base-image pull, Linux build, dependency install,
image checks, Harbor push, runtime Secret injection, and rollout verification.
Manual releases on the job branch deploy by default; deployment can be unchecked
for image-only publication. Both paths use committed source.
Runner capacity, corporate connectivity, and authorized Secrets are provisioned
once by platform maintainers and reused automatically, not configured by each user.