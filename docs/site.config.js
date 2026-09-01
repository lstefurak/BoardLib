window.BOARDLOG_CONFIG = {
  /*
   * The Lambda Function URL is not a secret; it is safe to ship in the public
   * page. The page holds NO secrets: the gate phrase is typed at runtime and
   * verified server-side by the Lambda, which answers with a short-lived
   * session token. Anyone can reach the URL, but without the phrase (or the
   * backend access key, which the page never sees) the backend returns nothing.
   */
  defaultEndpoint: "https://ngwxghoxrtdps64phpcowgjwym0demls.lambda-url.us-east-1.on.aws/",
};
