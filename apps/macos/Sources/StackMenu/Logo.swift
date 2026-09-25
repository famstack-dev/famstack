import AppKit

// ── The famstack mark ─────────────────────────────────────────────────────
//
// An "a" with two dots below it, on favicon.svg's 32-unit grid (y down). Two
// renderings: a monochrome template for the menu bar, which macOS tints to
// match it, and the coloured tile for the app icon.

enum Logo {
    private static let dotRadius: CGFloat = 2.8
    private static let dotCentres = [CGPoint(x: 11.5, y: 28), CGPoint(x: 20.5, y: 28)]

    private static let orange = NSColor(srgbRed: 0xF0 / 255, green: 0x7D / 255, blue: 0x45 / 255, alpha: 1)
    private static let teal = NSColor(srgbRed: 0x3D / 255, green: 0x8F / 255, blue: 0xA0 / 255, alpha: 1)
    private static let tile = NSColor(srgbRed: 0x16 / 255, green: 0x1F / 255, blue: 0x24 / 255, alpha: 1)

    /// The mark's own extent on the grid: the letter and both dots.
    private static var markBounds: CGRect {
        dotCentres.reduce(LogoOutline.letterA.boundingBoxOfPath) {
            $0.union(CGRect(x: $1.x - dotRadius, y: $1.y - dotRadius, width: 2 * dotRadius, height: 2 * dotRadius))
        }
    }

    // ── Menu bar ─────────────────────────────────────────────────────────

    enum State { case normal, attention, busy, unreachable }

    /// The mark as a template image. The state stays monochrome: a badge dot
    /// when something needs attention, hollow dots while an action runs,
    /// dimmed when the CLI does not answer.
    static func menuBarImage(_ state: State) -> NSImage {
        let size = NSSize(width: 18, height: 18)
        let image = NSImage(size: size, flipped: true) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            let mark = markBounds
            let scale = (rect.height - 2) / mark.height
            ctx.saveGState()
            ctx.translateBy(x: (rect.width - mark.width * scale) / 2 - mark.minX * scale,
                            y: 1 - mark.minY * scale)
            ctx.scaleBy(x: scale, y: scale)
            ctx.setAlpha(state == .unreachable ? 0.35 : 1)
            ctx.setFillColor(NSColor.black.cgColor)
            ctx.setStrokeColor(NSColor.black.cgColor)
            ctx.addPath(LogoOutline.letterA)
            ctx.fillPath()
            for centre in dotCentres {
                if state == .busy {
                    ctx.setLineWidth(1.3)
                    ctx.strokeEllipse(in: dotRect(centre, inset: 0.65))
                } else {
                    ctx.fillEllipse(in: dotRect(centre))
                }
            }
            ctx.restoreGState()
            if state == .attention {
                // A gap around the badge keeps it apart from the letter.
                let badge = CGRect(x: rect.maxX - 5.5, y: 0.5, width: 5, height: 5)
                ctx.setBlendMode(.clear)
                ctx.fillEllipse(in: badge.insetBy(dx: -1.2, dy: -1.2))
                ctx.setBlendMode(.normal)
                ctx.setFillColor(NSColor.black.cgColor)
                ctx.fillEllipse(in: badge)
            }
            return true
        }
        image.isTemplate = true
        return image
    }

    private static func dotRect(_ centre: CGPoint, inset: CGFloat = 0) -> CGRect {
        CGRect(x: centre.x - dotRadius, y: centre.y - dotRadius, width: 2 * dotRadius, height: 2 * dotRadius)
            .insetBy(dx: inset, dy: inset)
    }

    // ── App icon ─────────────────────────────────────────────────────────

    /// The favicon's tile as a macOS app icon: the body takes 824 of 1024
    /// points with Apple's corner radius, the mark keeps its place on it.
    static func drawAppIcon(in ctx: CGContext, side: CGFloat) {
        let unit = side / 1024
        let body = CGRect(x: 100 * unit, y: 100 * unit, width: 824 * unit, height: 824 * unit)
        ctx.addPath(CGPath(roundedRect: body, cornerWidth: 185 * unit, cornerHeight: 185 * unit, transform: nil))
        ctx.setFillColor(tile.cgColor)
        ctx.fillPath()

        ctx.saveGState()
        ctx.translateBy(x: body.minX, y: body.minY)
        ctx.scaleBy(x: body.width / 32, y: body.height / 32)
        ctx.addPath(LogoOutline.letterA)
        ctx.setFillColor(orange.cgColor)
        ctx.fillPath()
        ctx.setFillColor(teal.cgColor)
        for centre in dotCentres { ctx.fillEllipse(in: dotRect(centre)) }
        ctx.restoreGState()
    }

    /// The app icon as an image, for the panel header.
    static func appIcon(side: CGFloat) -> NSImage {
        NSImage(size: NSSize(width: side, height: side), flipped: true) { rect in
            guard let ctx = NSGraphicsContext.current?.cgContext else { return false }
            drawAppIcon(in: ctx, side: rect.width)
            return true
        }
    }

    /// Writes an `.iconset` folder; `iconutil -c icns` turns it into the
    /// bundle's icon (build.sh does).
    static func writeIconset(to folder: URL) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        for points in [16, 32, 128, 256, 512] {
            for scale in [1, 2] {
                let pixels = points * scale
                guard let rep = NSBitmapImageRep(
                    bitmapDataPlanes: nil, pixelsWide: pixels, pixelsHigh: pixels, bitsPerSample: 8,
                    samplesPerPixel: 4, hasAlpha: true, isPlanar: false, colorSpaceName: .deviceRGB,
                    bytesPerRow: 0, bitsPerPixel: 0),
                    let context = NSGraphicsContext(bitmapImageRep: rep) else { continue }
                // Flip to the grid's y-down orientation.
                let ctx = context.cgContext
                ctx.translateBy(x: 0, y: CGFloat(pixels))
                ctx.scaleBy(x: 1, y: -1)
                drawAppIcon(in: ctx, side: CGFloat(pixels))
                context.flushGraphics()
                let name = scale == 1 ? "icon_\(points)x\(points).png" : "icon_\(points)x\(points)@2x.png"
                try rep.representation(using: .png, properties: [:])?.write(to: folder.appendingPathComponent(name))
            }
        }
    }
}
