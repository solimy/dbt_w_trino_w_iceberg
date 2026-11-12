import json
import tempfile
import unittest
from pathlib import Path

from src.platform_manifest import compile_platform


MANIFEST = json.loads((Path(__file__).parent / "fixtures/platform.json").read_text())


class PlatformManifestTest(unittest.TestCase):
    def test_compiles_same_group_for_keycloak_and_opa(self):
        identity, policy = compile_platform(MANIFEST, "secret")
        consumer = identity["clients"]["pilot-consumer"]["group"]
        self.assertEqual(consumer, "/teams/finance/members")
        output = policy["schemas"]["mydata_prd.ventes__commandes_public"]["outputs"]["mart_country_daily_sales_eur"]
        self.assertEqual(output["readers"], [consumer])
        self.assertEqual(output["masks"], {"country_name": {"unmasked_groups": []}})
        self.assertEqual(policy["schemas"]["mydata_dev.ventes__commandes_internal"]["readers"],
                         ["/teams/commerce/maintainers"])

    def test_rejects_unknown_team_and_ambiguous_identifier(self):
        for key, team in (("ventes/commandes", "unknown"), ("ventes/commandes__other", "finance")):
            manifest = {**MANIFEST, "products": {key: {
                "dbt_project": "data_products/test_dp_2/dbt",
                "outputs": {"sales": {"reader_teams": [team]}},
            }}}
            with self.subTest(key=key), self.assertRaises(ValueError):
                compile_platform(manifest, "secret")

    def test_publish_grants_can_be_withheld_and_revoked(self):
        _, policy = compile_platform(MANIFEST, "secret", grant_consumers=False)
        output = policy["schemas"]["mydata_prd.ventes__commandes_public"]["outputs"]["mart_country_daily_sales_eur"]
        self.assertEqual(output["readers"], [])
        self.assertIn("country_name", output["masks"])

    def test_intermediate_bundle_keeps_only_existing_desired_grants(self):
        from src.platform import staged_policy

        _, previous = compile_platform(MANIFEST, "secret")
        _, desired = compile_platform(MANIFEST, "secret")
        name = "mydata_prd.ventes__commandes_public"
        output = "mart_country_daily_sales_eur"
        desired["schemas"][name]["outputs"][output]["readers"].append("/teams/commerce/members")
        self.assertEqual(staged_policy(previous, desired)["schemas"][name]["outputs"][output]["readers"],
                         ["/teams/finance/members"])
        self.assertEqual(staged_policy({}, desired)["schemas"][name]["outputs"][output]["readers"], [])

    def test_intermediate_bundle_retains_previous_masks_until_validation(self):
        from src.platform import staged_policy

        _, previous = compile_platform(MANIFEST, "secret")
        _, desired = compile_platform(MANIFEST, "secret")
        name = "mydata_prd.ventes__commandes_public"
        output = "mart_country_daily_sales_eur"
        desired["schemas"][name]["outputs"][output]["masks"] = {}
        self.assertIn("country_name", staged_policy(previous, desired)["schemas"][name]["outputs"][output]["masks"])

    def test_cached_policy_requires_the_active_opa_revision(self):
        from src.platform import cached_policy

        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "policy.json"
            previous = {"schemas": {"test": {"readers": ["finance"]}}}
            path.write_text(json.dumps({"revision": "before-revoke", "policy": previous}))
            self.assertEqual(cached_policy(path, "before-revoke"), previous)
            self.assertEqual(cached_policy(path, "after-revoke"), {})

    def test_console_fixtures_keep_finance_and_owner_memberships_separate(self):
        from src.platform_manifest import console_fixture

        users = console_fixture(MANIFEST)
        self.assertEqual(users, {
            "console-finance": ("finance", "members"),
            "console-owner": ("commerce", "maintainers"),
        })
        self.assertEqual(console_fixture({**MANIFEST, "products": {}}), {})

    def test_local_users_keep_initial_passwords_out_of_the_manifest(self):
        from src.platform_manifest import local_console_users

        manifest = {**MANIFEST, "local_users": {
            "alice": {"email": "alice@example.test", "first_name": "Alice", "last_name": "Example"},
        }}
        with self.assertRaisesRegex(ValueError, "alice"):
            local_console_users(manifest, {})
        users = local_console_users(manifest, {"alice": "initial-password"})
        self.assertEqual(users["alice"]["password"], "initial-password")
        self.assertTrue(users["alice"]["temporary"])
        self.assertNotIn("password", manifest["local_users"]["alice"])
