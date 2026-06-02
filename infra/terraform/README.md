# Terraform Deployment

This Terraform creates the AWS side of the BoardLog app:

- IAM role for Lambda execution (plus read/decrypt on the two secret parameters)
- CloudWatch log group with retention
- Python Lambda function
- Lambda Function URL with CORS restricted to your GitHub Pages origin
- A resource-based permission allowing public invoke of the Function URL

## Security model

There are **two independent secrets**, each stored as an **SSM SecureString**
(KMS-encrypted) and verified server-side by the Lambda:

| Secret        | Header sent by the page | Default SSM parameter   | Purpose                          |
| ------------- | ----------------------- | ----------------------- | -------------------------------- |
| Gate phrase   | `X-Board-Gate`          | `/boardlog/gate-phrase` | Unlocks the page UI (the "knock")|
| Access key    | `X-Board-Room-Key`      | `/boardlog/access-key`  | Authorizes the export request    |

Neither secret is stored in the static site or in terraform state. The user
types them at runtime; the Lambda reads them from SSM at request time. Because
they are separate parameters, you can **rotate either one independently**
without redeploying or touching the other.

## 1. Build the Lambda Zip

From the repo root:

```powershell
.\scripts\package_lambda.ps1
```

This writes `build\boardlog-lambda.zip` with Linux-compatible wheels plus the
local `boardlib` and backend code.

## 2. Create the two secrets (out-of-band)

These are created with the AWS CLI, not Terraform, so their plaintext never
enters terraform state. Use long random values:

```powershell
aws ssm put-parameter --name "/boardlog/gate-phrase" --type SecureString `
  --value "moonboard-at-midnight"
aws ssm put-parameter --name "/boardlog/access-key" --type SecureString `
  --value "a-long-random-access-key"
```

To **rotate** either secret later, just overwrite that one parameter — nothing
else changes:

```powershell
aws ssm put-parameter --name "/boardlog/gate-phrase" --type SecureString `
  --overwrite --value "a-new-gate-phrase"
```

The next Lambda cold start picks up the new value (or force it immediately by
publishing a new function version / updating any env var).

## 3. Configure Terraform

```powershell
Copy-Item infra\terraform\terraform.tfvars.example infra\terraform\terraform.tfvars
```

Edit `infra\terraform\terraform.tfvars` — the only required value is your exact
GitHub Pages origin (not a full path):

```hcl
allowed_origin = "https://your-user.github.io"
```

Override `access_key_param_name` / `gate_phrase_param_name` only if you used
non-default SSM parameter names.

## 4. Deploy

From `infra/terraform`:

```powershell
terraform init
terraform apply
```

Terraform prints `function_url`. Put that URL (it is not a secret) in
`docs/site.config.js` as `defaultEndpoint`.

## Notes

- A public Function URL (`authorization_type = NONE`) requires the
  `aws_lambda_permission` declared here. Without it, AWS returns
  `403 AccessDeniedException` on every invocation while CORS preflight still
  succeeds — a confusing failure mode this config fixes.
- Do not enable request body logging. Tension passwords are sent to Lambda for
  one request and should not be stored.
- Keep `allowed_boards = "tension"` unless you deliberately add more
  Aurora-backed boards.
- First request after a cold start may take longer because the Lambda downloads
  and syncs the board database into `/tmp/boardlog`.
- If packaging fails for `python3.13`, install a newer pip or change both the
  Terraform `runtime` and script `PythonRuntime` to a supported runtime.
