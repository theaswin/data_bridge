/** @odoo-module **/

import { registry } from "@web/core/registry";

registry.category("actions").add("full_browser_reload", async () => {
    window.location.reload();
});
