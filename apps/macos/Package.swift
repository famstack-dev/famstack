// swift-tools-version: 5.10
import PackageDescription

let package = Package(
    name: "StackMenu",
    platforms: [.macOS(.v14)],
    targets: [
        .executableTarget(name: "StackMenu", path: "Sources/StackMenu"),
    ]
)
