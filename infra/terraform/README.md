# Terraform Deployment

This Terraform creates the AWS side of the BoardLog app:

- IAM role for Lambda execution
- CloudWatch log group with retention
- Python Lambda function
- Lambda Function URL with CORS restricted to your GitHub Pages origin

The Lambda is intentionally public at the Function URL layer, but the handler can require `X-Board-Room-Key` by setting `boardlog_access_key`.

## 1. Build the Lambda Zip

From the repo root:

```powershell
.\scripts\package_lambda.ps1
```

This writes:

```text
build\boardlog-lambda.zip
```

The script downloads Linux-compatible Python wheels for Lambda and copies the local `boardlib` and backend code into the zip.

## 2. Configure Terraform

Copy the example vars file:

```powershell
Copy-Item infra\terraform\terraform.tfvars.example infra\terraform\terraform.tfvars
```

Edit `infra\terraform\terraform.tfvars`:

```hcl
allowed_origin      = "https://your-user.github.io"
boardlog_access_key = "a-long-random-shared-key"
```

Use the exact GitHub Pages origin, not a full path.

## 3. Deploy

From `infra/terraform`:

```powershell
terraform init
terraform apply
```

Terraform prints `function_url`. Put that URL in `docs/site.config.js`:

```js
window.BOARDLOG_CONFIG = {
  gateHash: "",
  defaultEndpoint: "https://your-function-url.lambda-url.us-east-1.on.aws/",
};
```

The "knock" phrase entered on the GitHub Pages app is sent as `X-Board-Room-Key`. For the backend to accept it, it must exactly match `boardlog_access_key`.

## Notes

- Do not enable request body logging. Tension passwords are sent to Lambda for one request and should not be stored.
- Keep `allowed_boards = "tension"` unless you deliberately add more Aurora-backed boards.
- First request after a cold start may take longer because the Lambda downloads and syncs the board database into `/tmp/boardlog`.
- If packaging fails for `python3.13`, install a newer pip or change both the Terraform `runtime` and script `PythonRuntime` to a Lambda-supported Python runtime.
