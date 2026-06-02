window.BOARDLOG_CONFIG = {
  /*
   * The Lambda Function URL is not a secret; it is safe to ship in the public
   * page. The page holds NO secrets: the gate phrase and access key are typed
   * at runtime and verified server-side by the Lambda. Anyone can reach the
   * URL, but without both secrets the backend returns nothing.
   */
  defaultEndpoint: "https://ngwxghoxrtdps64phpcowgjwym0demls.lambda-url.us-east-1.on.aws/",
};
