use ratatui::style::Color;

pub const BRAILLE: &[char] = &['⠋', '⠙', '⠹', '⠸', '⠼', '⠴', '⠦', '⠧', '⠇', '⠏'];

pub const AGENT_COLORS: &[Color] = &[
    Color::Rgb(255, 159, 0),   // #ff9f00 orange  — claude
    Color::Rgb(167, 139, 250), // #a78bfa purple  — copilot
    Color::Rgb(52, 211, 153),  // #34d399 green   — gemini
    Color::Rgb(244, 114, 182), // #f472b6 pink
    Color::Rgb(96, 165, 250),  // #60a5fa blue
];

pub const CYAN: Color = Color::Rgb(0, 215, 255);   // ◉ start node
pub const MINT: Color = Color::Rgb(0, 255, 159);   // ◆ consensus
pub const DIM: Color = Color::DarkGray;

pub fn hex_to_color(hex: &str) -> Color {
    let h = hex.trim_start_matches('#');
    if h.len() != 6 {
        return Color::White;
    }
    let r = u8::from_str_radix(&h[0..2], 16).unwrap_or(255);
    let g = u8::from_str_radix(&h[2..4], 16).unwrap_or(255);
    let b = u8::from_str_radix(&h[4..6], 16).unwrap_or(255);
    Color::Rgb(r, g, b)
}

/// Return color for agent at position `idx` in sorted agent list.
pub fn color_for_agent(_name: &str, idx: usize) -> Color {
    AGENT_COLORS[idx % AGENT_COLORS.len()]
}

pub fn braille_frame(frame: u32) -> char {
    BRAILLE[(frame as usize) % BRAILLE.len()]
}
