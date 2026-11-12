"""Labels for label printers (app/labels.py): a Brother's or a Zebra's
label sizes, one label a page, ZPL for a Zebra, and sending it to a Zebra
on the lab's network."""
from __future__ import annotations

import socket
import threading

# tests.base first: it points the app at a throwaway database before app is imported.
from tests.base import AppTestCase, execute, uniq
from tests.test_inventory import InventoryCase
from app import labels  # noqa: E402


class FakeZebra:
    """A printer on port 9100, as far as anyone sending to it can tell."""

    def __init__(self):
        self.server = socket.socket()
        self.server.bind(("127.0.0.1", 0))
        self.server.listen(1)
        self.port = self.server.getsockname()[1]
        self.received = b""
        self.thread = threading.Thread(target=self._take, daemon=True)
        self.thread.start()

    def _take(self):
        conn, _ = self.server.accept()
        with conn:
            while chunk := conn.recv(65536):
                self.received += chunk

    def close(self):
        self.thread.join(timeout=5)
        self.server.close()


class Labels(InventoryCase):
    def tearDown(self):
        execute("delete from app_settings where key like 'label_printer%'")
        super().tearDown()

    def test_a_label_printer_size_prints_one_label_a_page(self):
        cage = self.make_cage(self.a, uniq("C"))
        html = self.get_ok(self.a, f"/labels/cards/cages?ids={cage}&stock=62x29")
        self.assertIn("size: 62mm 29mm", html)
        self.assertIn("break-after: page", html)
        self.assertIn("Brother QL", html)
        # Remembered for next time, for cages.
        self.assertIn('value="62x29" selected', self.get_ok(self.a, f"/labels/cards/cages?ids={cage}"))

    def test_zpl_for_a_zebra(self):
        code = uniq("C^")
        cage = self.make_cage(self.a, code)
        r = self.a.get(f"/labels/cards/cages?ids={cage}&stock=51x25&format=zpl&dpi=203")
        self.assertEqual(r.status_code, 200)
        self.assertIn("attachment", r.headers["Content-Disposition"])
        zpl = r.get_data(as_text=True)
        self.assertTrue(zpl.startswith("^XA"))
        self.assertIn("^PW408", zpl)                          # 51 mm at 8 dots a mm
        self.assertIn("^BQN,2,", zpl)                         # the QR code
        self.assertIn(code.replace("^", "_5E"), zpl)         # ZPL's own characters escaped
        self.assertEqual(zpl.count("^XZ"), 1)

    def test_vials_tubes_and_tanks(self):
        key = self.make_stock_module(self.a)
        vial = self.make_vial(self.a, key, genotype="w; UAS-GFP")
        html = self.get_ok(self.a, f"/labels/cards/stocks/{key}?ids={vial}")
        self.assertIn("w; UAS-GFP", html)
        self.assertIn(f"Labels", self.get_ok(self.a, f"/stocks/{key}"))
        item = self.make_item(self.a, "reagents", name=uniq("Buffer "), lot="L-77")
        html = self.get_ok(self.a, f"/labels/cards/inventory/reagents?selected_ids={item}")
        self.assertIn("L-77", html)
        self.assertIn(f'id="item-{item}"', self.get_ok(self.a, "/inventory/reagents"))
        tank = self.make_tank(self.a)
        self.assertIn("Tank ", self.get_ok(self.a, f"/labels/cards/tanks?ids={tank}"))

    def test_a_label_s_qr_code_scales_to_its_box_instead_of_being_cut(self):
        item = self.make_item(self.a, "reagents", uniq("anti-RFP "))
        html = self.get_ok(self.a, f"/labels/cards/inventory/reagents?selected_ids={item}")
        svg = html.split('<div class="label-qr">', 1)[1].split(">", 1)[0]
        self.assertIn('viewBox="0 0', svg)
        self.assertNotIn("width=", svg)            # the label's CSS sizes it, the whole code

    def test_a_qr_code_on_its_own_is_an_svg_file_an_img_can_draw(self):
        from app import labels
        if not labels.qr_svg_file("x"):
            self.skipTest("segno is not installed")
        r = self.a.get("/labels/qr.svg?d=hello")
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.get_data(as_text=True).startswith('<svg xmlns="http://www.w3.org/2000/svg"'))

    def test_a_cryo_label_says_what_you_choose_and_can_wrap(self):
        box = self.make_rack(self.a, "samples", uniq("−80 A R1 B"))
        item = self.make_item(self.a, "samples", name=uniq("S01-R V-6h rep1 "), rack_id=str(box), position="B1")
        page = f"/labels/cards/inventory/samples?ids={item}&stock=33x13"
        html = self.get_ok(self.a, page + "&fields_set=1&f=position&f=printed&wrap=1")
        body = html.split('class="card-sheet"', 1)[1]
        self.assertIn(">B1<", body.replace(" ", ""))
        self.assertNotIn("Owner", body)
        self.assertIn("-webkit-line-clamp: 2", html)
        # Remembered for this database next time.
        again = self.get_ok(self.a, f"/labels/cards/inventory/samples?ids={item}&stock=33x13")
        self.assertIn('value="position" checked', again)
        self.assertNotIn('value="owner" checked', again)
        crowded = self.get_ok(self.a, page + "&fields_set=1&f=box&f=position&f=owner&f=printed&wrap=1")
        self.assertIn("Not on these labels, for lack of room", crowded)
        zpl = self.a.get(page + "&format=zpl").get_data(as_text=True)
        self.assertIn(",2,0,L^FH_^FD#", zpl)                  # the title in a two-line block

    def test_only_a_printer_on_the_lab_network(self):
        self.assertEqual(labels.printer_address("192.168.1.50"), ("192.168.1.50", 9100))
        self.assertEqual(labels.printer_address("127.0.0.1:9101"), ("127.0.0.1", 9101))
        self.assertIsNone(labels.printer_address("8.8.8.8"))
        self.assertIsNone(labels.printer_address("printer:abc"))
        self.assertEqual(self.m.post("/labels/printer", data={"address": "127.0.0.1"}).status_code, 403)
        self.a.post("/labels/printer", data={"address": "8.8.8.8", "back": "/labels/cards/cages"})
        self.assertNotIn("Send to Zebra", self.get_ok(self.a, "/labels/cards/cages"))

    def test_sent_straight_to_the_zebra(self):
        zebra = FakeZebra()
        try:
            self.a.post("/labels/printer", data={"address": f"127.0.0.1:{zebra.port}", "dpi": "300",
                                                 "back": "/labels/cards/cages"})
            cage = self.make_cage(self.a, uniq("C"))
            page = self.get_ok(self.m, f"/labels/cards/cages?ids={cage}")
            self.assertIn("Send to Zebra", page)            # anyone in the lab may print
            r = self.m.post("/labels/send", data={"kind": "cages", "ids": str(cage), "stock": "51x25",
                                                  "back": f"/labels/cards/cages?ids={cage}"})
            self.assertEqual(r.status_code, 302)
        finally:
            zebra.close()
        self.assertTrue(zebra.received.startswith(b"^XA"))
        self.assertIn(b"^PW612", zebra.received)              # 51 mm at 300 dpi
