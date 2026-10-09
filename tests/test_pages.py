"""Every page renders.

Each GET route that takes no URL parameters is requested as an admin, as a
member and logged out; then the main database pages (every view of every
database, with records on them) and the detail pages of records."""
from tests.base import *  # noqa: F401,F403
from tests.base import AppTestCase, GRID_NAMING, one, rows, uniq, days_ago

import unittest

from app.app import ZEBRAFISH_VIEWS, app
from app.models import COLONY_VIEWS
from app.organism_routes import MODULE_VIEWS
from app.stock_routes import VIEWS as STOCK_VIEWS

# Not pages: they end the session, or hand off to Google.
SKIP = {"/logout", "/calendar/google/connect", "/calendar/google/callback",
        # A computer's key, not a session, opens these (tests/test_lab_copy.py);
        # the status is the desktop app's only.
        "/api/lab-copy/snapshot", "/api/lab-copy/files", "/lab-copy/status",
        # The desktop app's only (tests/test_server_setup.py, tests/test_start_new_lab.py).
        "/server-setup/", "/start-new-lab",
        # Before any account, in the desktop app (tests/test_door.py).
        "/open-lab"}
# /: the sign-in before signing in (app/door.py); /guest: entering a guest code (app/guests.py);
# /ask: asking the lab's admins; /joined/status: whether this browser's request to join is approved.
PUBLIC = {"/", "/login", "/register", "/healthz", "/guest", "/ask", "/joined/status", "/favicon.ico",
          # OAuth metadata, which an assistant app reads before signing in (app/oauth.py)
          "/.well-known/oauth-authorization-server", "/.well-known/oauth-protected-resource",
          "/.well-known/oauth-protected-resource/api/v1/mcp"}


def simple_get_routes() -> list[str]:
    return sorted({rule.rule for rule in app.url_map.iter_rules()
                   if "GET" in rule.methods and not rule.arguments
                   and not rule.rule.startswith("/static") and rule.rule not in SKIP})


class PagesRender(AppTestCase):
    @classmethod
    def setUpClass(cls):
        """Something on every page, so the rows are rendered too."""
        super().setUpClass()
        a, m = cls.a, cls.m
        h = cls()  # the factories are instance methods
        # colony
        cls.colony = h.make_colony(a, cls.admin, n_mice=2, purpose="Breeding")
        h.make_colony(m, cls.member, n_mice=1)
        cls.exp_name = uniq("Exp ")
        a.post("/colony/experiments/create", data={"name": cls.exp_name, "from_cage_id": cls.colony["cage"]})
        cls.experiment = one("select id from experiments where name=?", cls.exp_name)
        a.post("/colony/strains/create", data={"strain_name": uniq("Strain ")})
        # zebrafish
        cls.line = cls.make_line(a)
        cls.tank = cls.make_tank(a, line_id_fk=cls.line)
        # plasmids
        box = cls.make_box(a)
        cls.plasmid = cls.make_plasmid(a, box_id=box, position="A1")
        # inventory
        for key in ("samples", "orders", "reagents", "antibodies"):
            cls.make_item(a, key)
        # fly and worm stocks
        cls.fly, _, rack = h.make_fly_setup(a)
        h.make_vial(a, cls.fly, rack_id=rack, position="A1")
        h.make_vial(a, cls.fly, purpose="cross", genotype="", female_genotype="w", male_genotype="y")
        cls.worm = cls.make_stock_module(a, "worm")
        h.make_vial(a, cls.worm, purpose="maintenance")
        # a custom organism database
        cls.org = cls.make_organism_module(a)
        housing = h.make_housing(a, cls.org, owner=cls.admin)
        h.make_animal(a, cls.org, housing_id_fk=housing, owner=cls.admin)

    def assertAllRender(self, client, urls, ok=lambda code: code < 500 and code != 404):
        bad = []
        for url in urls:
            code = client.get(url).status_code
            if not ok(code):
                bad.append((url, code))
        self.assertEqual(bad, [], f"{len(bad)} of {len(urls)} pages failed")

    # ------------------------------------------------------------ every simple route
    def test_every_simple_page_renders_for_an_admin(self):
        self.assertAllRender(self.a, simple_get_routes())

    def test_every_simple_page_renders_for_a_member(self):
        self.assertAllRender(self.m, simple_get_routes())

    def test_every_page_but_login_and_register_needs_a_login(self):
        anonymous = app.test_client()
        needing = [u for u in simple_get_routes() if u not in PUBLIC]
        let_in = [u for u in needing if anonymous.get(u).status_code == 200]
        self.assertEqual(let_in, [])

    def test_login_and_register_render_logged_out(self):
        anonymous = app.test_client()
        for url in PUBLIC:
            self.assertEqual(anonymous.get(url).status_code, 200, url)

    def test_there_are_enough_routes_for_the_sweep_to_mean_something(self):
        self.assertGreater(len(simple_get_routes()), 30)

    # ------------------------------------------------------------ database pages
    def test_every_colony_view_renders_in_every_scope(self):
        urls = [f"/colony?view={view}&scope={scope}" for view, _ in COLONY_VIEWS for scope in ("mine", "all")]
        for client in (self.a, self.m):
            self.assertAllRender(client, urls, ok=lambda code: code == 200)

    def test_every_zebrafish_view_renders(self):
        urls = [f"/zebrafish?view={view}" for view in ZEBRAFISH_VIEWS]
        for client in (self.a, self.m):
            self.assertAllRender(client, urls, ok=lambda code: code == 200)

    def test_every_view_of_a_fly_and_a_worm_database_renders(self):
        urls = [f"/stocks/{key}?view={view}" for key in (self.fly, self.worm, "drosophila", "c_elegans")
                for view in STOCK_VIEWS]
        for client in (self.a, self.m):
            self.assertAllRender(client, urls, ok=lambda code: code == 200)

    def test_every_view_of_a_custom_organism_database_renders(self):
        urls = [f"/organisms/{self.org}?view={view}" for view, _, _ in MODULE_VIEWS]
        for client in (self.a, self.m):
            self.assertAllRender(client, urls, ok=lambda code: code in (200, 302))

    def test_every_inventory_database_and_its_settings_render(self):
        keys = [k for (k,) in rows("select key from inventory_modules")]
        urls = [f"/inventory/{k}" for k in keys] + [f"/inventory/{k}?scope=mine" for k in keys]
        self.assertAllRender(self.a, urls + [f"/inventory/{k}/configure" for k in keys],
                             ok=lambda code: code == 200)
        # A member opens the lab's inventories and their own, not other
        # people's personal ones or other project groups' (those are not
        # found, by design).
        mine = [k for (k,) in rows("select key from inventory_modules where private_to in ('', ?) and "
                                   "(share_group_id is null or share_group_id in (select group_id_fk "
                                   "from lab_group_members where username = ?))", self.member, self.member)]
        self.assertAllRender(self.m, [f"/inventory/{k}" for k in mine] + [f"/inventory/{k}?scope=mine" for k in mine],
                             ok=lambda code: code == 200)

    def test_the_add_a_database_pages_render_for_each_preset(self):
        urls = ["/organisms/new", "/organisms/new?preset=custom", "/organisms/new?preset=mouse",
                "/organisms/new?preset=zebrafish", "/stocks/new?kind=fly", "/stocks/new?kind=worm",
                "/inventory/new", "/inventory/new?preset=custom", "/inventory/new?preset=reagents",
                "/inventory/new?preset=antibodies"]
        self.assertAllRender(self.a, urls, ok=lambda code: code == 200)

    def test_utilities_has_the_calculators_and_the_lab_s_chemicals(self):
        html = self.get_ok(self.m, "/utilities")
        self.assertIn("bench-calcs.js", html)
        self.assertIn('"name": "NaCl"', html)          # the lab's list, for the chemical picker

    # ------------------------------------------------------------ detail pages
    def test_record_detail_pages_render(self):
        mouse_number = one("select mouse_id from mice where id=?", self.colony["mice"][0])
        plasmid_number = one("select plasmid_id from plasmids where id=?", self.plasmid)
        urls = [f"/colony/experiments/{self.experiment}", f"/plasmid/{plasmid_number}",
                f"/plasmids/{self.plasmid}/sequence.json", f"/zebrafish/lines/{self.line}",
                f"/zebrafish/tanks/{self.tank}/card", "/labels/cards/cages", f"/labels/cards/{self.org}",
                "/labels/qr.svg?d=hello", f"/notebook/backlinks/mouse/{self.colony['mice'][0]}",
                f"/notebook/lookup/mouse/{mouse_number}", f"/notebook/lookup/plasmid/{plasmid_number}"]
        for client in (self.a, self.m):
            self.assertAllRender(client, urls, ok=lambda code: code == 200)

    def test_search_renders_with_and_without_hits(self):
        for q in ("", "a", self.exp_name, "zzz-nothing-matches", "<script>"):
            self.assertEqual(self.a.get("/search", query_string={"q": q}).status_code, 200, q)

    def test_a_missing_record_is_not_a_server_error(self):
        urls = ["/colony/experiments/987654321", "/plasmids/987654321", "/plasmid/987654321", "/zebrafish/lines/987654321",
                "/zebrafish/tanks/987654321/card", "/organisms/no-such-db", "/stocks/no-such-db",
                "/inventory/no-such-db"]
        self.assertAllRender(self.a, urls, ok=lambda code: code < 500)

    def test_a_database_that_does_not_answer_gets_a_page_not_a_bare_500(self):
        from unittest import mock
        from sqlalchemy.exc import OperationalError
        import app.app as app_module
        boom = OperationalError("SELECT 1", {}, Exception("database is locked"))
        with mock.patch.object(app_module, "colony_context", side_effect=boom):
            r = self.a.get("/colony?view=mice")
            self.assertEqual(r.status_code, 503)
            self.assertIn("The database is not answering", r.get_data(as_text=True))
            r = self.a.get("/colony?view=mice", headers={"X-Autosave": "1"})
            self.assertEqual((r.status_code, r.get_json()["ok"]), (503, False))

    def test_search_is_case_blind_in_every_script_and_takes_percent_literally(self):
        tag = uniq("Δ-Cre Café ")
        self.make_colony(self.a, self.admin, n_mice=1)
        mid = one("select max(id) from mice")
        execute("update mice set genotype=?, transgene_1=? where id=?", tag, tag, mid)
        found = lambda q: [r["label"] for r in self.a.get("/search", query_string={"q": q}).get_json()["results"]]
        self.assertTrue(found(tag.lower().replace("café", "CAFÉ")))
        self.assertEqual(found(tag.replace(" ", "%", 1)), [])     # "Δ-Cre%Café" is not "Δ-Cre Café"
        self.assertEqual(found(tag.replace(" ", "_", 1)), [])
        self.assertEqual(self.a.get("/search", query_string={"q": "1" * 21}).status_code, 200)

    def test_search_puts_names_that_start_with_it_first(self):
        tag = uniq("Q")                      # a word nobody else has
        for n in range(12):                  # a dozen that only mention it, newer than the tubes
            self.make_item(self.a, "samples", name=uniq("Column "), notes=f"ran on {tag}00")
        for n in range(2):
            self.make_item(self.m, "samples", name=f"{tag}-R{n}")
        for n in range(12):
            self.make_item(self.a, "samples", name=uniq("Column "), notes=f"ran on {tag}00")
        labels = [r["label"] for r in self.m.get("/search", query_string={"q": tag}).get_json()["results"]]
        self.assertTrue(labels and f"{tag}-R" in labels[0] and f"{tag}-R" in labels[1], labels)

    def test_mangled_numbers_in_an_address_are_not_server_errors(self):
        huge = "9" * 21
        for url in (f"/notebook?page={huge}", f"/notebook?tab=x", "/notebook/search/mouse?limit=x",
                    f"/stocks/drosophila?horizon={huge}", "/calendar/events.json?start=0001-01-01&end=0001-02-01",
                    "/calendar/events.json?start=9999-12-01&end=9999-12-31"):
            self.assertLess(self.a.get(url).status_code, 500, url)

    def test_a_wrong_address_gets_the_app_s_own_page(self):
        r = self.a.get("/no/such/page")
        self.assertEqual(r.status_code, 404)
        self.assertIn("There&#39;s nothing here", r.get_data(as_text=True))

    def test_exports_download_as_files(self):
        r = self.a.get("/colony/mice/export")
        self.assertEqual(r.status_code, 200)
        self.assertIn("csv", r.headers.get("Content-Type", "") + r.headers.get("Content-Disposition", ""))
        self.assertEqual(self.m.get("/settings/export").status_code, 200)


if __name__ == "__main__":
    unittest.main()
