(function () {
    var menus = document.querySelectorAll(".dashboard-dropdown");
    if (!menus.length) return;

    menus.forEach(function (menu) {
        var summary = menu.querySelector("summary");
        var value = menu.querySelector(".dashboard-dropdown-value");

        summary.addEventListener("click", function (event) {
            if (menu.hasAttribute("data-locked")) event.preventDefault();
        });

        menu.addEventListener("change", function (event) {
            var chosen = Array.prototype.map.call(
                menu.querySelectorAll("input:checked"),
                function (input) { return input.parentElement.textContent.trim(); }
            );
            value.textContent = chosen.length ? chosen.join(", ") : "None";
            if (event.target.type === "radio") {
                menu.open = false;
                summary.focus();
            }
        });
    });

    document.addEventListener("click", function (event) {
        menus.forEach(function (menu) {
            if (menu.open && !menu.contains(event.target)) menu.open = false;
        });
    });

    document.addEventListener("keydown", function (event) {
        if (event.key !== "Escape") return;
        menus.forEach(function (menu) {
            if (!menu.open) return;
            menu.open = false;
            menu.querySelector("summary").focus();
        });
    });
})();
