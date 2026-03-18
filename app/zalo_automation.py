from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any

from app.config import load_app_config
from app.selectors import load_selectors

try:
    import uiautomator2 as u2
except Exception:  # pragma: no cover
    u2 = None

log = logging.getLogger(__name__)


@dataclass
class AutomationResult:
    ok: bool
    status: str
    message: str = ""


class ZaloAutomation:
    def __init__(self, adb_serial: str, dry_run: bool = True) -> None:
        self.serial = adb_serial
        self.dry_run = dry_run
        self.selectors = load_selectors()
        self.device = None
        cfg = load_app_config().global_
        self.tap_delay = cfg.tap_delay_seconds * 2.0
        self.type_delay = cfg.type_delay_seconds * 2.0
        self.swipe_delay = cfg.swipe_delay_seconds * 2.0
        self.step_delay = cfg.step_delay_seconds * 2.0

    def ensure_device(self) -> None:
        if self.dry_run:
            return
        if u2 is None:
            raise RuntimeError("uiautomator2 unavailable")
        if self.device is None:
            self.device = u2.connect(self.serial)

    def _exists_any(self, selector_defs: list[dict[str, Any]]) -> bool:
        if self.device is None:
            return False
        for item in selector_defs:
            if self.device(**item).exists:
                return True
        return False

    def _get_first(self, selector_defs: list[dict[str, Any]]):
        if self.device is None:
            return None
        for item in selector_defs:
            obj = self.device(**item)
            if obj.exists:
                return obj
        return None

    def _click_first(self, selector_defs: list[dict[str, Any]]) -> bool:
        obj = self._get_first(selector_defs)
        if obj is None:
            return False
        obj.click()
        return True

    def _set_text_first(self, selector_defs: list[dict[str, Any]], value: str) -> bool:
        obj = self._get_first(selector_defs)
        if obj is None:
            return False
        try:
            obj.click()
        except Exception:
            pass
        obj.set_text(value)
        return True

    def _fill_login_field(
        self,
        selector_key: str,
        value: str,
        fallback_xy: tuple[int, int],
        adb: Any = None,
    ) -> bool:
        """Click a login field, clear it, then paste value.

        Strategy:
        1. ADB tap exact coordinates to guarantee the correct field is focused.
        2. Find the focused EditText via u2 and use set_text to clear + paste.
        3. Fallback: ADB keyboard if u2 fails.
        """
        # Step 1: ADB tap to focus the correct field
        if adb is not None:
            adb.tap(fallback_xy[0], fallback_xy[1])
            log.info("Tapped field %s at %s", selector_key, fallback_xy)
            time.sleep(self.tap_delay)

        # Step 2: Try u2 set_text on the focused element
        self.ensure_device()
        try:
            focused = self.device(focused=True, className="android.widget.EditText")
            if focused.exists:
                focused.set_text("")
                time.sleep(self.type_delay)
                focused.set_text(value)
                log.info("Field %s set via u2 focused EditText", selector_key)
                return True
        except Exception as exc:
            log.warning("u2 focused set_text failed for %s: %s", selector_key, exc)

        # Step 3: Fallback — ADB keyboard
        if adb is not None:
            log.info("Fallback: ADB keyboard for %s", selector_key)
            adb.force_adb_keyboard()
            adb.input_text_adb_keyboard_b64(value)
            return True

        return False

    def check_login_state(self, wait_seconds: int = 0) -> str:
        """Detect login state with optional wait for UI to settle.

        Priority order:
        1. Bottom tab bar (maintab_root_layout etc.) → definitely logged_in
        2. Login form fields (etPhoneNumber, etPass) → definitely logged_out
        3. Welcome screen (btnLogin + btnRegisterUsingPhoneNumber) → logged_out
        4. None of the above → unknown (UI still loading or unexpected screen)
        """
        if self.dry_run:
            return "logged_in"
        self.ensure_device()

        # Optionally wait and retry to let UI settle after app launch
        attempts = max(1, wait_seconds // 3) if wait_seconds > 0 else 1
        for attempt in range(attempts):
            if attempt > 0:
                time.sleep(3)
                log.info("check_login_state retry %d/%d...", attempt + 1, attempts)

            # Check 1: Bottom tab bar = logged in (most reliable)
            if self._exists_any(self.selectors.get("home_bottom_tabs", [])):
                return "logged_in"
            if self._exists_any(self.selectors.get("home_feed_markers", [])):
                return "logged_in"

            # Check 2: Login form input fields = login screen
            if self._exists_any(self.selectors.get("login_markers", [])):
                return "logged_out"

            # Check 3: Welcome screen (first launch)
            has_welcome_login = self._exists_any(self.selectors.get("welcome_login_button", []))
            has_welcome_register = self._exists_any(self.selectors.get("welcome_register_button", []))
            if has_welcome_login and has_welcome_register:
                return "logged_out"

        return "unknown"

    def login_if_needed(self, phone: str, password: str, adb=None) -> AutomationResult:
        if self.dry_run:
            return AutomationResult(True, "success", "dry-run login skipped")
        self.ensure_device()
        state = self.check_login_state()
        if state == "logged_in":
            return AutomationResult(True, "success", "already logged in")
        if state == "unknown":
            log.warning("Login state is unknown — likely still on home/chat screen. Treating as logged_in.")
            return AutomationResult(True, "success", "state unknown, assuming logged in")

        has_welcome_login = self._exists_any(self.selectors.get("welcome_login_button", []))
        has_welcome_register = self._exists_any(self.selectors.get("welcome_register_button", []))
        if has_welcome_login and has_welcome_register:
            welcome_clicked = self._click_first(self.selectors.get("welcome_login_button", []))
            if welcome_clicked:
                log.info("Clicked welcome login button on startup screen")
                time.sleep(self.step_delay)
            elif adb is not None:
                log.info("Welcome login selector found but click did not fire; using fallback tap")
                adb.tap(288, 897)
                time.sleep(self.step_delay)
            else:
                log.info("Welcome login selector found but no adb fallback available")
        else:
            log.info("Welcome login pair not present; trying credential form directly")

        if not phone or not password:
            return AutomationResult(False, "needs_manual_action", "missing phone/password in config after opening login screen")

        # SAFETY CHECK: Verify login form is actually showing before typing credentials
        # This prevents accidentally pasting password into a chat window
        time.sleep(self.step_delay)
        has_phone_field = self._exists_any([{"resourceId": "com.zing.zalo:id/etPhoneNumber"}])
        has_password_field = self._exists_any([{"resourceId": "com.zing.zalo:id/etPass"}])
        if not has_phone_field and not has_password_field:
            log.error("SAFETY: Login form fields NOT found on screen! Aborting login to prevent typing into wrong screen.")
            # Re-check — maybe we're actually logged in
            recheck = self.check_login_state()
            if recheck == "logged_in":
                return AutomationResult(True, "success", "actually logged in after recheck")
            return AutomationResult(False, "failed", "login form not visible, refusing to type credentials")

        # --- Phone input: tap field → clear → paste phone ---
        phone_ok = self._fill_login_field(
            "login_phone_input", phone, fallback_xy=(288, 186), adb=adb,
        )
        log.info("Phone input: ok=%s", phone_ok)
        time.sleep(self.step_delay)

        # --- Password input: tap field → clear → paste password ---
        password_ok = self._fill_login_field(
            "login_password_input", password, fallback_xy=(263, 243), adb=adb,
        )
        log.info("Password input: ok=%s", password_ok)
        time.sleep(self.step_delay)

        submit_ok = self._click_first(self.selectors.get("login_submit_button", []))
        if not submit_ok and adb is not None:
            adb.keyevent("66")
            time.sleep(self.step_delay)
            submit_ok = True
        if not submit_ok and adb is not None:
            adb.tap(527, 952)
            time.sleep(self.step_delay)
            submit_ok = True

        if not (phone_ok and password_ok and submit_ok):
            return AutomationResult(False, "needs_manual_action", "login controls not found")

        time.sleep(max(5, self.step_delay * 3))
        new_state = self.check_login_state()
        if new_state == "logged_in":
            return AutomationResult(True, "success", "login success")
        return AutomationResult(False, "needs_manual_action", f"login state after submit: {new_state}")

    # ------------------------------------------------------------------
    # Text insertion (extracted from old create_post)
    # ------------------------------------------------------------------
    def _insert_text(self, text: str, adb=None) -> bool:
        """Insert text into the composer EditText. Returns True on success."""
        if not str(text or "").strip():
            log.warning("Post text is empty")
            return False

        content_obj = self._get_first(self.selectors.get("post_text_input", []))
        if content_obj is None:
            log.warning("Post text input not found")
            return False

        typed = False
        try:
            info = getattr(content_obj, "info", {}) or {}
            bounds = info.get("bounds") or {}
            if adb is not None and all(k in bounds for k in ("left", "right", "top", "bottom")):
                cx = int((bounds["left"] + bounds["right"]) / 2)
                cy = int((bounds["top"] + bounds["bottom"]) / 2)
                adb.tap(cx, cy)
            else:
                content_obj.click()
            time.sleep(self.tap_delay)
            content_obj.set_text("")
            time.sleep(self.type_delay)
            content_obj.set_text(text)
            time.sleep(self.type_delay)
            info = getattr(content_obj, "info", {}) or {}
            current_text = str(info.get("text") or "")
            if current_text and current_text != "Bạn đang nghĩ gì?":
                log.info("Post content set via uiautomator2")
                typed = True
        except Exception:
            typed = False

        if not typed and adb is not None:
            log.info("Trying focused composer paste fallback")
            try:
                info = getattr(content_obj, "info", {}) or {}
                bounds = info.get("bounds") or {}
                if all(k in bounds for k in ("left", "right", "top", "bottom")):
                    cx = int((bounds["left"] + bounds["right"]) / 2)
                    cy = int((bounds["top"] + bounds["bottom"]) / 2)
                    adb.tap(cx, cy)
                else:
                    content_obj.click()
            except Exception:
                try:
                    content_obj.click()
                except Exception:
                    pass
            time.sleep(self.tap_delay)
            adb.force_adb_keyboard()
            adb.input_text_adb_keyboard_b64(text)
            time.sleep(self.type_delay)
            try:
                refreshed = self._get_first(self.selectors.get("post_text_input", []))
                info = getattr(refreshed or content_obj, "info", {}) or {}
                current_text = str(info.get("text") or "")
                typed = bool(current_text and current_text != "Bạn đang nghĩ gì?")
            except Exception:
                typed = False

        if not typed:
            log.warning("Post text was not inserted into composer")
        return typed

    # ------------------------------------------------------------------
    # Gallery image selection — ADB tap with fixed grid coordinates
    # ------------------------------------------------------------------
    def _select_images_in_gallery(self, image_count: int, adb=None) -> bool:
        """Select images in the gallery picker using ADB tap on grid coordinates.

        Gallery layout (verified from actual UI dump on 576x1024 screen):
          recycler_view bounds: [0,87][576,1024]
          3-column grid, each column 192px wide, row height 192px
          ┌──────────┬──────────┬──────────┐
          │ Chụp ảnh │ Image 1  │ Image 2  │  row 0  y=87..279
          │ [0-192]  │[192-384] │[384-576] │
          ├──────────┼──────────┼──────────┤
          │ Image 3  │ Image 4  │ Image 5  │  row 1  y=279..471
          └──────────┴──────────┴──────────┘

        Position 0 (row0/col0) = camera "Chụp ảnh" → SKIP
        Checkbox circle at top-right corner of each image cell.
        Tap target: (cell_right - 25, cell_top + 25)

        Calculated checkbox positions:
          Image 1: pos=1, col=1, row=0 → (384-25, 87+25)  = (359, 112)
          Image 2: pos=2, col=2, row=0 → (576-25, 87+25)  = (551, 112)
          Image 3: pos=3, col=0, row=1 → (192-25, 279+25) = (167, 304)
          Image 4: pos=4, col=1, row=1 → (384-25, 279+25) = (359, 304)
          ...
        """
        if adb is None:
            log.warning("ADB required for gallery image selection")
            return False

        # Wait for gallery to load
        time.sleep(self.step_delay)

        # Grid constants (from actual UI dump)
        COL_COUNT = 3
        COL_WIDTH = 192   # each column is 192px
        ROW_HEIGHT = 192  # each row is 192px
        ROW_START_Y = 87  # first row starts at y=87 (below action bar)
        CB_OFFSET_R = 25  # checkbox is 25px from right edge of cell
        CB_OFFSET_T = 25  # checkbox is 25px from top edge of cell

        selected = 0
        pos = 1  # start from position 1 (skip position 0 = camera)

        while selected < image_count:
            col = pos % COL_COUNT
            row = pos // COL_COUNT

            # Scroll if we need images beyond visible area (~4.5 rows visible)
            if row >= 4:
                log.info("Scrolling gallery down to reveal more images (row=%d)", row)
                adb.swipe(288, 700, 288, 200, 300)
                time.sleep(self.swipe_delay)
                # After scroll, approximate: 3 rows scrolled up, so adjust
                # We restart counting from visible area
                row -= 3
                # Recalculate checkpoint Y after scroll

            cell_right = (col + 1) * COL_WIDTH
            cell_top = ROW_START_Y + row * ROW_HEIGHT
            cx = cell_right - CB_OFFSET_R
            cy = cell_top + CB_OFFSET_T

            log.info("Selecting image %d/%d: tap(%d, %d) [pos=%d row=%d col=%d]",
                     selected + 1, image_count, cx, cy, pos, row, col)
            adb.tap(cx, cy)
            time.sleep(self.tap_delay)
            selected += 1
            pos += 1

        log.info("Image selection done: %d/%d selected", selected, image_count)
        return selected > 0

    # ------------------------------------------------------------------
    # Dismiss layout popup
    # ------------------------------------------------------------------
    def _dismiss_layout_popup(self, adb=None) -> None:
        """Dismiss the 'Chọn bố cục' popup that appears after confirming gallery.

        From actual UI dump:
          - imv_close button bounds: [508,827][556,856] → tap(532, 842)
          - Popup title "Chọn bố cục" at [229,827][347,856]
        """
        time.sleep(self.step_delay)

        # Try clicking the close button via selector (imv_close)
        if self._click_first(self.selectors.get("layout_popup_close", [])):
            log.info("Dismissed layout popup via close button selector")
            time.sleep(self.tap_delay)
            return

        # Fallback: tap the X close button at exact coordinates
        if adb is not None:
            log.info("Dismissing layout popup via fallback tap on X button (532, 842)")
            adb.tap(532, 842)
            time.sleep(self.tap_delay)

    # ------------------------------------------------------------------
    # Main create_post — restructured: images FIRST, text SECOND
    # ------------------------------------------------------------------
    def create_post(self, text: str, image_count: int, adb=None) -> AutomationResult:
        if self.dry_run:
            time.sleep(1)
            return AutomationResult(True, "success", f"dry-run posted {image_count} images")
        self.ensure_device()
        if self.device is None:
            return AutomationResult(False, "failed", "uiautomator2 unavailable")

        # Step 1: Click Timeline tab — wait extra for Timeline to load
        log.info("Clicking Timeline tab...")
        self._click_first(self.selectors.get("timeline_tab", []))
        time.sleep(self.step_delay * 2)  # extra wait for Timeline to fully load

        if image_count > 0:
            # Step 2: Click "Ảnh" button on Timeline → opens gallery picker
            photo_clicked = self._click_first(self.selectors.get("photo_button_on_timeline", []))
            if not photo_clicked:
                log.info("Photo button selector not found, using fallback tap")
                if adb is not None:
                    # "Ảnh" button parent root_container bounds [14,183][133,226]
                    adb.tap(74, 205)
                else:
                    return AutomationResult(False, "failed", "photo button not found on timeline")
            time.sleep(self.step_delay)

            # Step 3: Select images in gallery
            images_ok = self._select_images_in_gallery(image_count, adb)
            if not images_ok:
                return AutomationResult(False, "failed", "image selection in gallery failed")

            # Step 4: Confirm gallery selection (blue checkmark FAB at bottom-right)
            confirm_clicked = self._click_first(self.selectors.get("gallery_confirm_button", []))
            if not confirm_clicked:
                log.info("Gallery confirm button selector not found, using fallback tap at bottom-right FAB")
                if adb is not None:
                    # Blue circle FAB with checkmark is at bottom-right corner
                    adb.tap(545, 985)
                else:
                    return AutomationResult(False, "failed", "gallery confirm button not found")
            time.sleep(self.step_delay)

            # Step 5: Dismiss "Chọn bố cục" popup
            self._dismiss_layout_popup(adb)

            # Step 6: Insert text into composer
            typed = self._insert_text(text, adb)
            if not typed:
                return AutomationResult(False, "failed", "post text was not inserted into composer")
        else:
            # No images: use standard compose flow
            compose_ready = self._exists_any(self.selectors.get("post_text_input", []))
            if not compose_ready:
                if not self._click_first(self.selectors.get("create_post_button", [])):
                    return AutomationResult(False, "failed", "create post button not found")
                time.sleep(self.step_delay)

            typed = self._insert_text(text, adb)
            if not typed:
                return AutomationResult(False, "failed", "post text was not inserted into composer")

        time.sleep(self.type_delay)

        # Step 7: Click "Đăng" (submit)
        submit_ok = self._click_first(self.selectors.get("submit_post_button", []))
        if not submit_ok and adb is not None:
            adb.tap(535, 58)
            time.sleep(self.step_delay)
            submit_ok = True
        if not submit_ok:
            return AutomationResult(False, "failed", "submit post button not found")

        time.sleep(max(4, self.step_delay * 2))
        return AutomationResult(True, "success", f"posted with {image_count} images")
