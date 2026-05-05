import os
import shutil
import pytest
from unittest.mock import patch, MagicMock
from wallpaper_automator.resource.base_resource import _BaseResourceMeta, BaseResource


class MockResource(BaseResource):
    def mount(self, allow_compress: bool = True):
        pass

    def demount(self, allow_compress: bool = True):
        pass


class TestBaseResource:
    def test_metaclass_initialization(self):
        """Metaclass initializes the base cache directory only once."""
        with patch("os.makedirs") as mock_makedirs:
            _BaseResourceMeta._base_cache_initialized = False

            class TestSub1(BaseResource):
                def mount(self, allow_compress=True):
                    pass

                def demount(self, allow_compress=True):
                    pass

            class TestSub2(BaseResource):
                def mount(self, allow_compress=True):
                    pass

                def demount(self, allow_compress=True):
                    pass

            assert mock_makedirs.call_count >= 1
            called_paths = [args[0] for args, kwargs in mock_makedirs.call_args_list]
            assert any("wallpaper_automator_cache" in p for p in called_paths)

    def test_instance_unique_cache_dir(self):
        """Each instance gets its own UUID-based cache directory."""
        res1 = MockResource(temp_dir=True)
        res2 = MockResource(temp_dir=True)

        assert res1.cache_dir != res2.cache_dir
        assert os.path.basename(res1.cache_dir) != os.path.basename(res2.cache_dir)

        shutil.rmtree(res1.cache_dir)
        shutil.rmtree(res2.cache_dir)

    def test_no_temp_dir_behavior(self):
        """temp_dir=False raises ValueError when accessing cache_dir."""
        res = MockResource(temp_dir=False)
        with pytest.raises(ValueError, match="cache dir is unavailable"):
            _ = res.cache_dir

    def test_cleanup_base_cache(self):
        """Metaclass cleanup static method removes the base cache directory."""
        with patch("shutil.rmtree") as mock_rmtree:
            with patch("os.path.exists", return_value=True):
                _BaseResourceMeta._cleanup_base_cache()
                mock_rmtree.assert_called_with(_BaseResourceMeta._base_cache_dir)


class TestResource:
    def test_real_directory_creation(self, tmp_path):
        """Verify a real directory is created on the filesystem."""
        res = MockResource(temp_dir=True)
        path = res.cache_dir

        assert os.path.exists(path)
        assert os.path.isdir(path)

        shutil.rmtree(path)
