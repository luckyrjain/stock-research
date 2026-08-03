import os
import unittest

from cryptography.fernet import Fernet

from core.crypto import EncryptionNotConfigured, decrypt, encrypt


class CryptoRoundTripTest(unittest.TestCase):
    def setUp(self):
        self._old_key = os.environ.get("PORTFOLIO_ENCRYPTION_KEY")
        os.environ["PORTFOLIO_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

    def tearDown(self):
        if self._old_key is None:
            os.environ.pop("PORTFOLIO_ENCRYPTION_KEY", None)
        else:
            os.environ["PORTFOLIO_ENCRYPTION_KEY"] = self._old_key

    def test_round_trip(self):
        ciphertext = encrypt("super-secret-access-token")
        self.assertNotEqual(ciphertext, "super-secret-access-token")
        self.assertEqual(decrypt(ciphertext), "super-secret-access-token")

    def test_ciphertext_not_readable_without_decrypt(self):
        ciphertext = encrypt("kite-access-token-xyz")
        self.assertNotIn("kite-access-token-xyz", ciphertext)


class CryptoMisconfiguredTest(unittest.TestCase):
    def setUp(self):
        self._old_key = os.environ.pop("PORTFOLIO_ENCRYPTION_KEY", None)

    def tearDown(self):
        if self._old_key is not None:
            os.environ["PORTFOLIO_ENCRYPTION_KEY"] = self._old_key

    def test_encrypt_raises_when_key_unset(self):
        with self.assertRaises(EncryptionNotConfigured):
            encrypt("anything")

    def test_decrypt_raises_when_key_unset(self):
        with self.assertRaises(EncryptionNotConfigured):
            decrypt("anything")

    def test_invalid_key_raises_encryption_not_configured(self):
        os.environ["PORTFOLIO_ENCRYPTION_KEY"] = "not-a-valid-fernet-key"
        with self.assertRaises(EncryptionNotConfigured):
            encrypt("anything")


if __name__ == "__main__":
    unittest.main()
