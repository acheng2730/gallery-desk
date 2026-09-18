require 'cairo'

function conky_round_corners()
    if conky_window == nil then return end

    local cs = cairo_xlib_surface_create(
        conky_window.display,
        conky_window.drawable,
        conky_window.visual,
        conky_window.width,
        conky_window.height
    )
    local cr = cairo_create(cs)

    local w = conky_window.width
    local h = conky_window.height
    local r = 15

    local pi = math.pi

    -- Build rounded rectangle path
    cairo_new_path(cr)
    cairo_arc(cr, r, r, r, pi, 1.5 * pi)
    cairo_arc(cr, w - r, r, r, 1.5 * pi, 2 * pi)
    cairo_arc(cr, w - r, h - r, r, 0, 0.5 * pi)
    cairo_arc(cr, r, h - r, r, 0.5 * pi, pi)
    cairo_close_path(cr)

    -- DEST_IN keeps everything conky drew (background + text + graphs)
    -- only inside the rounded rectangle; corners become transparent
    cairo_set_operator(cr, CAIRO_OPERATOR_DEST_IN)
    cairo_set_source_rgba(cr, 1, 1, 1, 1)
    cairo_fill(cr)

    cairo_destroy(cr)
    cairo_surface_destroy(cs)
end
