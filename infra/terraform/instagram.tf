# Private staging bucket for the Instagram uploader (tools/instagram_publish.py).
#
# Meta has to download each Reel from a URL it can reach, so the tool uploads
# the clip here, hands Meta a short-lived presigned link, and deletes the
# object as soon as the post is published (or fails). The lifecycle rule is a
# backstop so nothing lingers if a run dies halfway. Nothing in this bucket is
# ever public: presigned links are the only way in.

resource "aws_s3_bucket" "instagram_staging" {
  bucket = var.instagram_staging_bucket != "" ? var.instagram_staging_bucket : "boardlog-instagram-staging-${data.aws_caller_identity.current.account_id}"
}

resource "aws_s3_bucket_public_access_block" "instagram_staging" {
  bucket                  = aws_s3_bucket.instagram_staging.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "instagram_staging" {
  bucket = aws_s3_bucket.instagram_staging.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "instagram_staging" {
  bucket = aws_s3_bucket.instagram_staging.id

  rule {
    id     = "expire-staged-clips"
    status = "Enabled"

    filter {}

    expiration {
      days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}
