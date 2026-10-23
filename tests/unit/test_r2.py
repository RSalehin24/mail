import hashlib
import unittest

from host.r2 import R2Options, parse_listing
from host.s3_signing import S3Credentials, S3Request, SigV4, canonical_query
from shared.errors import ServiceError


class R2PolicyTests(unittest.TestCase):
    def configuration(self):
        return {
            "inputs": {"backup_destination": "s3://fixture-bucket/mail-admin/"},
            "backup_r2": {
                "endpoint": "https://" + "a" * 32 + ".r2.cloudflarestorage.com",
                "credentials_path": "/private/fixture-credentials.json",
                "max_archive_bytes": 1024,
                "max_bucket_bytes": 4096,
                "retention_seconds": 7 * 86400,
            },
        }

    def test_sigv4_matches_published_aws_get_vanilla_vector(self):
        # AWS SDK test vector: botocore/tests/unit/auth/aws4_testsuite/get-vanilla.
        credentials = S3Credentials(
            "AKIDEXAMPLE", "wJalrXUtnFEMI/K7MDENG+bPxRfiCYEXAMPLEKEY"
        )
        request = S3Request("GET", "/", {
            "Host": "example.amazonaws.com", "X-Amz-Date": "20150830T123600Z",
        })
        observed = SigV4(credentials, ("us-east-1", "service")).authorization(request)
        self.assertEqual(observed, (
            "AWS4-HMAC-SHA256 Credential=AKIDEXAMPLE/20150830/us-east-1/service/aws4_request, "
            "SignedHeaders=host;x-amz-date, "
            "Signature=5fa00fa31553b73ebf1942676e86291e8372ff2a2260956d9b8aae1d763fbf31"
        ))
        self.assertNotIn(credentials.secret_access_key, repr(credentials))

    def test_s3_query_encodes_spaces_and_continuation_tokens_before_sorting(self):
        self.assertEqual(
            canonical_query({"prefix": "mail-admin/", "token": "a+/= x", "list-type": "2"}),
            "list-type=2&prefix=mail-admin%2F&token=a%2B%2F%3D%20x",
        )

    def test_only_private_r2_https_endpoints_and_dedicated_prefixes_are_accepted(self):
        original = self.configuration()
        self.assertEqual(R2Options.load(original).prefix, "mail-admin/")
        for destination in (
            "s3://fixture-bucket/", "s3://fixture-bucket/../outside", "s3://user@bucket/mail/",
            "s3://fixture-bucket/mail/?key=value", "s3://fixture-bucket/mail/#fragment",
        ):
            configuration = self.configuration()
            configuration["inputs"]["backup_destination"] = destination
            with self.subTest(destination=destination), self.assertRaises(ServiceError):
                R2Options.load(configuration)
        for endpoint in (
            "http://" + "a" * 32 + ".r2.cloudflarestorage.com", "https://127.0.0.1",
            "https://user:secret@" + "a" * 32 + ".r2.cloudflarestorage.com",
            "https://" + "a" * 32 + ".r2.cloudflarestorage.com.evil.example",
        ):
            configuration = self.configuration()
            configuration["backup_r2"]["endpoint"] = endpoint
            with self.subTest(endpoint=endpoint), self.assertRaises(ServiceError):
                R2Options.load(configuration)

    def test_missing_nonpositive_or_over_allowance_limits_fail_closed(self):
        for key, value in (
            ("max_archive_bytes", 0), ("max_archive_bytes", True),
            ("max_bucket_bytes", 9_000_000_001), ("retention_seconds", None),
            ("max_archive_bytes", 4097), ("credentials_path", "relative.json"),
        ):
            configuration = self.configuration()
            configuration["backup_r2"][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ServiceError):
                R2Options.load(configuration)

    def test_malformed_unknown_or_entity_containing_listings_fail_closed(self):
        for body in (
            b"not XML", b"<ListBucketResult/>", b"<!DOCTYPE x><x/>",
            b'<ListBucketResult xmlns="http://s3.amazonaws.com/doc/2006-03-01/">'
            b"<IsTruncated>true</IsTruncated></ListBucketResult>",
        ):
            with self.subTest(digest=hashlib.sha256(body).hexdigest()):
                with self.assertRaisesRegex(ServiceError, "invalid_r2_listing"):
                    parse_listing(body)
