#import <Cocoa/Cocoa.h>
#import <CoreGraphics/CoreGraphics.h>
#import <sys/file.h>
#import <fcntl.h>
#import <unistd.h>

@interface MurasamePanel : NSPanel
@property(nonatomic, copy) NSString *imagePath;
@property(nonatomic, copy) NSString *textPath;
@property(nonatomic, copy) NSString *statePath;
@property(nonatomic, copy) NSString *qtVisibilityPath;
@property(nonatomic, copy) NSString *commandPath;
@property(nonatomic) NSPoint dragOffset;
@property(nonatomic) NSDate *lastImageDate;
@property(nonatomic) NSDate *lastTextDate;
@property(nonatomic) BOOL dragging;
@property(nonatomic) BOOL fullscreen;
@property(nonatomic) BOOL qtVisible;
@property(nonatomic) BOOL shown;
@property(nonatomic, strong) NSTextField *textField;
@property(nonatomic, copy) NSString *inputBuffer;
@property(nonatomic) BOOL inputMode;
@end

@implementation MurasamePanel
- (BOOL)canBecomeKeyWindow { return YES; }
- (BOOL)canBecomeMainWindow { return NO; }
- (BOOL)acceptsFirstResponder { return YES; }
- (void)mouseDown:(NSEvent *)event {
    if (event.type == NSEventTypeLeftMouseDown && (event.modifierFlags & NSEventModifierFlagOption)) {
        self.dragOffset = event.locationInWindow;
        self.dragging = YES;
    } else if (event.type == NSEventTypeOtherMouseDown) {
        self.dragOffset = event.locationInWindow;
        self.dragging = YES;
    } else if (event.type == NSEventTypeLeftMouseDown && self.fullscreen) {
        // 与 Qt 桌宠一致：点击下半身进入输入模式。
        if (event.locationInWindow.y < self.frame.size.height * 0.38) {
            self.inputMode = YES;
            self.inputBuffer = @"";
            [self updateInputText];
            [self makeKeyAndOrderFront:nil];
            [NSApp activateIgnoringOtherApps:YES];
        }
    }
}
- (void)mouseDragged:(NSEvent *)event {
    if (!self.dragging) return;
    NSPoint mouse = [NSEvent mouseLocation];
    NSPoint origin = self.frame.origin;
    origin.x = mouse.x - self.dragOffset.x;
    origin.y = mouse.y - self.dragOffset.y;
    [self setFrameOrigin:origin];
}
- (void)mouseUp:(NSEvent *)event { self.dragging = NO; }
- (void)otherMouseDown:(NSEvent *)event {
    self.dragOffset = event.locationInWindow;
    self.dragging = YES;
}
- (void)otherMouseUp:(NSEvent *)event { self.dragging = NO; }
- (void)rightMouseDown:(NSEvent *)event {
    if (!self.fullscreen) return;
    NSMenu *menu = [[NSMenu alloc] initWithTitle:@"丛雨"];
    NSMenuItem *exitItem = [[NSMenuItem alloc] initWithTitle:@"退出全屏兼容模式" action:@selector(disableOverlay:) keyEquivalent:@"^"];
    exitItem.target = self;
    [menu addItem:exitItem];
    [NSMenu popUpContextMenu:menu withEvent:event forView:self.contentView];
}
- (void)disableOverlay:(id)sender {
    if (self.commandPath) {
        [@"__native_overlay_disable__" writeToFile:self.commandPath atomically:YES encoding:NSUTF8StringEncoding error:nil];
    }
}
- (void)keyDown:(NSEvent *)event {
    if (!self.inputMode) {
        [super keyDown:event];
        return;
    }
    NSString *characters = event.charactersIgnoringModifiers ?: @"";
    if (event.keyCode == 36 || event.keyCode == 76) {
        NSString *text = [self.inputBuffer stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet];
        if (text.length > 0 && self.commandPath) {
            [text writeToFile:self.commandPath atomically:YES encoding:NSUTF8StringEncoding error:nil];
        }
        self.inputMode = NO;
        self.inputBuffer = @"";
        [self updateInputText];
    } else if (event.keyCode == 51 || event.keyCode == 117) {
        if (self.inputBuffer.length > 0) self.inputBuffer = [self.inputBuffer substringToIndex:self.inputBuffer.length - 1];
        [self updateInputText];
    } else if (characters.length > 0 && ![characters hasPrefix:@"\e"]) {
        self.inputBuffer = [self.inputBuffer stringByAppendingString:characters];
        [self updateInputText];
    }
}
- (void)updateInputText {
    if (!self.textField) return;
    NSString *shown = self.inputMode
        ? [NSString stringWithFormat:@"【主人】\n  「%@」", self.inputBuffer.length ? self.inputBuffer : @"..."]
        : @"";
    self.textField.stringValue = shown;
}
@end

static void reloadImageIfChanged(MurasamePanel *panel, NSImageView *imageView) {
    NSDictionary *attributes = [[NSFileManager defaultManager] attributesOfItemAtPath:panel.imagePath error:nil];
    NSDate *modified = attributes[NSFileModificationDate];
    if (!modified || (panel.lastImageDate && [modified compare:panel.lastImageDate] != NSOrderedDescending)) {
        return;
    }
    NSImage *image = [[NSImage alloc] initWithContentsOfFile:panel.imagePath];
    if (image) {
        imageView.image = image;
        panel.lastImageDate = modified;
    }
}

static void reloadTextIfChanged(MurasamePanel *panel, NSTextField *textField) {
    if (!panel.textPath) return;
    NSDictionary *attributes = [[NSFileManager defaultManager] attributesOfItemAtPath:panel.textPath error:nil];
    NSDate *modified = attributes[NSFileModificationDate];
    if (!modified || (panel.lastTextDate && [modified compare:panel.lastTextDate] != NSOrderedDescending)) return;
    NSString *text = [NSString stringWithContentsOfFile:panel.textPath encoding:NSUTF8StringEncoding error:nil];
    if (text) {
        textField.stringValue = text;
        panel.lastTextDate = modified;
    }
}

static BOOL frontmostAppHasFullscreenWindow(NSApplication *app) {
    NSRunningApplication *frontmost = NSWorkspace.sharedWorkspace.frontmostApplication;
    if (!frontmost || frontmost.processIdentifier == getpid()) return NO;
    NSArray *windows = CFBridgingRelease(CGWindowListCopyWindowInfo(
        kCGWindowListOptionOnScreenOnly | kCGWindowListExcludeDesktopElements,
        kCGNullWindowID));
    CGFloat screenWidth = NSScreen.mainScreen.frame.size.width;
    CGFloat screenHeight = NSScreen.mainScreen.frame.size.height;
    pid_t pid = frontmost.processIdentifier;
    for (NSDictionary *info in windows) {
        if ([info[(id)kCGWindowOwnerPID] intValue] != pid) continue;
        NSDictionary *bounds = info[(id)kCGWindowBounds];
        CGRect rect;
        if (!CGRectMakeWithDictionaryRepresentation((__bridge CFDictionaryRef)bounds, &rect)) continue;
        // Retina/不同 macOS 版本会让窗口边界存在少量误差；全屏窗口
        // 通常会覆盖屏幕绝大多数区域，使用比例判断比固定像素更可靠。
        if (rect.size.width >= screenWidth * 0.90 && rect.size.height >= screenHeight * 0.90) return YES;
    }
    return NO;
}

static void updateFullscreenState(MurasamePanel *panel) {
    BOOL fullscreen = frontmostAppHasFullscreenWindow(NSApp);
    if (fullscreen != panel.fullscreen) {
        panel.fullscreen = fullscreen;
        NSString *value = fullscreen ? @"1\n" : @"0\n";
        [value writeToFile:panel.statePath atomically:YES encoding:NSUTF8StringEncoding error:nil];
    }
    BOOL shouldShow = panel.fullscreen || !panel.qtVisible;
    if (shouldShow == panel.shown) return;
    panel.shown = shouldShow;
    if (shouldShow) [panel orderFrontRegardless]; else [panel orderOut:nil];
}

static void reloadQtVisibility(MurasamePanel *panel) {
    if (!panel.qtVisibilityPath) return;
    NSString *value = [NSString stringWithContentsOfFile:panel.qtVisibilityPath encoding:NSUTF8StringEncoding error:nil];
    if (value) panel.qtVisible = [value stringByTrimmingCharactersInSet:NSCharacterSet.whitespaceAndNewlineCharacterSet].integerValue != 0;
}

int main(int argc, const char *argv[]) {
    @autoreleasepool {
        NSString *imagePath = argc > 1
            ? [NSString stringWithUTF8String:argv[1]]
            : @"assets/murasame_startup_original.png";
        NSString *textPath = argc > 2 ? [NSString stringWithUTF8String:argv[2]] : nil;
        NSString *statePath = argc > 3 ? [NSString stringWithUTF8String:argv[3]] : @"tmp/native_overlay_fullscreen.state";
        NSString *qtVisibilityPath = argc > 4 ? [NSString stringWithUTF8String:argv[4]] : @"tmp/native_overlay_qt_visible.state";
        NSString *commandPath = argc > 5 ? [NSString stringWithUTF8String:argv[5]] : @"tmp/native_overlay_command.txt";
        int lockFD = open(".native_overlay/native_overlay.lock", O_CREAT | O_RDWR, 0600);
        if (lockFD < 0 || flock(lockFD, LOCK_EX | LOCK_NB) != 0) return 0;
        NSImage *image = [[NSImage alloc] initWithContentsOfFile:imagePath];
        if (!image) {
            fprintf(stderr, "无法读取丛雨图片: %s\n", imagePath.UTF8String);
            return 1;
        }

        [NSApplication sharedApplication];
        [NSApp setActivationPolicy:NSApplicationActivationPolicyAccessory];

        NSSize imageSize = image.size;
        CGFloat maxHeight = NSScreen.mainScreen.visibleFrame.size.height;
        CGFloat scale = MIN(1.0, maxHeight * 0.65 / MAX(imageSize.height, 1));
        NSSize panelSize = NSMakeSize(imageSize.width * scale, imageSize.height * scale);
        NSRect screenFrame = NSScreen.mainScreen.visibleFrame;
        NSPoint origin = NSMakePoint(
            NSMaxX(screenFrame) - panelSize.width - 24,
            NSMinY(screenFrame) + 24
        );

        NSUInteger style = NSWindowStyleMaskBorderless | NSWindowStyleMaskNonactivatingPanel;
        MurasamePanel *panel = [[MurasamePanel alloc]
            initWithContentRect:NSMakeRect(origin.x, origin.y, panelSize.width, panelSize.height)
            styleMask:style
            backing:NSBackingStoreBuffered
            defer:NO];
        panel.imagePath = imagePath;
        panel.textPath = textPath;
        panel.statePath = statePath;
        panel.qtVisibilityPath = qtVisibilityPath;
        panel.commandPath = commandPath;
        panel.fullscreen = NO;
        panel.qtVisible = YES;
        panel.shown = NO;
        panel.opaque = NO;
        panel.backgroundColor = NSColor.clearColor;
        panel.hasShadow = NO;
        panel.hidesOnDeactivate = NO;
        panel.floatingPanel = YES;
        panel.level = 101;
        panel.collectionBehavior = NSWindowCollectionBehaviorCanJoinAllSpaces
            | NSWindowCollectionBehaviorCanJoinAllApplications
            | NSWindowCollectionBehaviorFullScreenAuxiliary
            | NSWindowCollectionBehaviorStationary;

        NSView *rootView = [[NSView alloc] initWithFrame:NSMakeRect(0, 0, panelSize.width, panelSize.height)];
        NSImageView *imageView = [[NSImageView alloc] initWithFrame:NSMakeRect(0, 0, panelSize.width, panelSize.height)];
        imageView.image = image;
        imageView.imageScaling = NSImageScaleProportionallyUpOrDown;
        imageView.imageAlignment = NSImageAlignCenter;
        NSTextField *textField = [[NSTextField alloc] initWithFrame:NSMakeRect(18, panelSize.height * 0.42, panelSize.width - 36, panelSize.height * 0.28)];
        textField.bezeled = NO;
        textField.drawsBackground = NO;
        textField.backgroundColor = NSColor.clearColor;
        textField.textColor = NSColor.whiteColor;
        textField.font = [NSFont systemFontOfSize:14 weight:NSFontWeightMedium];
        textField.alignment = NSTextAlignmentLeft;
        textField.editable = NO;
        textField.selectable = NO;
        textField.maximumNumberOfLines = 0;
        textField.usesSingleLineMode = NO;
        textField.lineBreakMode = NSLineBreakByWordWrapping;
        NSShadow *textShadow = [[NSShadow alloc] init];
        textShadow.shadowColor = [[NSColor blackColor] colorWithAlphaComponent:0.9];
        textShadow.shadowBlurRadius = 2.0;
        textShadow.shadowOffset = NSMakeSize(1, -1);
        textField.shadow = textShadow;
        [rootView addSubview:imageView];
        [rootView addSubview:textField];
        panel.contentView = rootView;
        panel.textField = textField;
        reloadImageIfChanged(panel, imageView);
        reloadTextIfChanged(panel, textField);
        [@"0\n" writeToFile:panel.statePath atomically:YES encoding:NSUTF8StringEncoding error:nil];
        [NSTimer scheduledTimerWithTimeInterval:0.5 repeats:YES block:^(NSTimer *timer) {
            reloadImageIfChanged(panel, imageView);
            reloadTextIfChanged(panel, textField);
            reloadQtVisibility(panel);
            updateFullscreenState(panel);
        }];
        [panel orderOut:nil];

        NSLog(@"[AIpet][native-overlay] NSPanel 已启动, behavior=canJoinAllApplications, level=101");
        [NSApp run];
    }
    return 0;
}
