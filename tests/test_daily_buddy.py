import unittest

from daily_buddy import BuddyError, _host_script, check_phone, parse_buddy_output


class PhoneTests(unittest.TestCase):
    def test_ten_digits_become_the_stored_form(self):
        self.assertEqual(check_phone("9876543211"), "+919876543211")
        self.assertEqual(check_phone("+91 83078 47231"), "+918307847231")

    def test_a_short_number_is_rejected(self):
        with self.assertRaises(BuddyError):
            check_phone("98765")


class ResultTests(unittest.TestCase):
    def test_a_pair_line_is_read_from_noisy_output(self):
        text = "CONTAINER abc\nLC_BUDDY PAIRED 2026-11-24 0 11111111-1111-1111-1111-111111111111\n"
        parsed = parse_buddy_output(text)
        self.assertEqual(parsed["code"], "PAIRED")
        self.assertEqual(parsed["day"], "2026-11-24")
        self.assertFalse(parsed["freed"])

    def test_an_open_buddy_is_reported(self):
        parsed = parse_buddy_output("LC_BUDDY BUSY +918307847231 +919999999999\n")
        self.assertEqual(parsed["code"], "BUSY")
        self.assertEqual(parsed["other"], "+919999999999")

    def test_the_dev_script_names_both_numbers(self):
        script = _host_script("+919876543211", "+918307847231")
        self.assertIn("--format '{{.ID}} {{.Image}}'", script)
        self.assertIn("/localcircle\\/chat:/", script)
        self.assertIn('LEFT = "+919876543211"', script)
        self.assertIn('RIGHT = "+918307847231"', script)
        self.assertNotIn("PHONE_LEFT", script)

    def test_output_without_a_result_line_is_rejected(self):
        with self.assertRaises(BuddyError):
            parse_buddy_output("something else")


if __name__ == "__main__":
    unittest.main()
