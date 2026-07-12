import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
BIN_DIR = ROOT / "bin"
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

from deploy_desktop_lib import (
    detect_llamacpp_build_profile,
    is_allowed_download_url,
    version_gte,
)


class DesktopDeployLibTests(unittest.TestCase):
    def test_version_gte_handles_semver_and_tool_banner(self):
        self.assertTrue(version_gte("git version 2.47.1.windows.1", "2.40"))
        self.assertTrue(version_gte("v22.3.0", "22.0"))
        self.assertFalse(version_gte("cmake version 3.18.2", "3.20"))

    def test_is_allowed_download_url_only_accepts_whitelist(self):
        allowed = ["huggingface.co", "modelscope.cn"]
        self.assertTrue(is_allowed_download_url("https://huggingface.co/foo/bar", allowed))
        self.assertTrue(is_allowed_download_url("https://modelscope.cn/models/demo", allowed))
        self.assertFalse(is_allowed_download_url("https://example.com/file.gguf", allowed))

    def test_detect_llamacpp_build_profile_prefers_cuda_when_nvidia_present(self):
        env_info = {
            "os": {"system": "windows"},
            "cpu": {"architecture": "amd64"},
            "gpu": {"has_nvidia": True, "has_amd": False, "has_apple_silicon": False},
        }
        profile = detect_llamacpp_build_profile(env_info)
        self.assertEqual(profile["backend"], "cuda")
        self.assertIn("-DGGML_CUDA=ON", profile["cmake_flags"])

    def test_detect_llamacpp_build_profile_falls_back_to_cpu_avx2(self):
        env_info = {
            "os": {"system": "linux"},
            "cpu": {"architecture": "x86_64"},
            "gpu": {"has_nvidia": False, "has_amd": False, "has_apple_silicon": False},
        }
        profile = detect_llamacpp_build_profile(env_info)
        self.assertEqual(profile["backend"], "cpu_avx2")
        self.assertIn("-DGGML_AVX2=ON", profile["cmake_flags"])


if __name__ == "__main__":
    unittest.main()
