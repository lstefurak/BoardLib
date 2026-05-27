window.BOARDLOG_CONFIG = {
  /*
   * Optional SHA-256 hash for the soft client-side knock.
   * Leave blank to let any phrase open the UI. The backend should still enforce
   * real access control with the X-Board-Room-Key header.
   */
  gateHash: "",
  /*
   * AWS Lambda Function URL for the JSON export backend.
   */
  defaultEndpoint: "https://ngwxghoxrtdps64phpcowgjwym0demls.lambda-url.us-east-1.on.aws/",
};
