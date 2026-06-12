"""product 资产类型的 spec 层行为：第 4 条目、列表字段抽象、全局库豁免。"""

from lib.asset_types import (
    ASSET_SPECS,
    ASSET_TYPES,
    BUCKET_KEY,
    GLOBAL_LIBRARY_ASSET_TYPES,
    SHEET_KEY,
)


class TestProductSpec:
    def test_product_is_fourth_asset_type(self):
        assert "product" in ASSET_SPECS
        spec = ASSET_SPECS["product"]
        assert spec.bucket_key == "products"
        assert spec.sheet_field == "product_sheet"
        assert spec.subdir == "products"

    def test_product_declares_list_and_string_fields(self):
        spec = ASSET_SPECS["product"]
        assert "brand" in spec.extra_string_fields
        assert set(spec.extra_list_fields) == {"reference_images", "selling_points"}

    def test_agent_whitelist_has_selling_points_not_reference_images(self):
        spec = ASSET_SPECS["product"]
        assert "selling_points" in spec.agent_editable_extra_fields
        assert "reference_images" not in spec.agent_editable_extra_fields

    def test_derived_constants_include_product(self):
        assert "product" in ASSET_TYPES
        assert BUCKET_KEY["product"] == "products"
        assert SHEET_KEY["product"] == "product_sheet"

    def test_product_excluded_from_global_library(self):
        assert "product" not in GLOBAL_LIBRARY_ASSET_TYPES
        assert GLOBAL_LIBRARY_ASSET_TYPES == frozenset({"character", "scene", "prop"})


class TestProductProjectLayout:
    def test_products_dir_allowed_at_project_root(self):
        from lib.data_validator import DataValidator

        assert "products" in DataValidator.ALLOWED_ROOT_ENTRIES

    def test_products_resource_path_resolvable(self):
        from lib.resource_paths import resource_relative_path

        assert resource_relative_path("products", "保温杯") == "products/保温杯.png"


class TestExistingSpecsUnchanged:
    def test_list_fields_default_empty_for_existing_types(self):
        for asset_type in ("character", "scene", "prop"):
            assert ASSET_SPECS[asset_type].extra_list_fields == ()

    def test_existing_types_stay_in_global_library(self):
        for asset_type in ("character", "scene", "prop"):
            assert asset_type in GLOBAL_LIBRARY_ASSET_TYPES
