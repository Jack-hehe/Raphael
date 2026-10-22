//! A compact accessibility snapshot of one window, built from any tree of elements.
//!
//! [`build`] walks the tree from the window's root, breadth first, so that a cap keeps the window's
//! structure (its toolbars and panes) before the depths of one list. It keeps what a model needs to
//! act and nothing more:
//!
//! - each element's control type, name, value, state (on/off, expanded/collapsed, selected),
//!   whether it is enabled and focused, what it can be done to, and where it is on the screen;
//! - **never the value of a password field**: such an element is marked `password` and shown
//!   without one, whatever the app would give;
//! - elements off the screen are left out with what is under them; a pane, a group, an image or a
//!   text with no name and nothing to do is left out too, though what is inside it stays (at the
//!   depth of what holds it);
//! - at most [`Caps::elements`] elements, [`Caps::depth`] levels and [`Caps::chars`] characters of
//!   names and values, each name and value cut to its own limit. A snapshot that had to leave some
//!   out says so (`truncated`).
//!
//! The ids are 1, 2, 3… in the order of the tree, given anew by every look. The elements behind
//! them are kept by the caller ([`Snapshot::nodes`], the same order) until the next look at that
//! window, so that a step can name one.
//!
//! The tree is a trait ([`UiNode`]): UI Automation in the app (`uia`), a fake one in the tests.
//! Nothing here logs a name or a value.

use std::collections::VecDeque;

use serde::Serialize;

use super::policy::clean;

/// What an element says about itself.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct Props {
    /// The UI Automation control type id (`UIA_ButtonControlTypeId` = 50000...).
    pub control_type: i32,
    pub name: String,
    /// The value of the Value pattern, if it has one.
    pub value: Option<String>,
    pub enabled: bool,
    pub focused: bool,
    pub password: bool,
    pub offscreen: bool,
    /// "on", "off", "mixed", "expanded", "collapsed" or "selected".
    pub state: Option<&'static str>,
    /// What can be done to it: "invoke", "toggle", "select", "expand", "value", "scroll".
    pub actions: Vec<&'static str>,
    /// Left, top, width, height, in physical pixels of the screen.
    pub rect: Option<[i32; 4]>,
}

/// An element of a tree.
pub trait UiNode: Sized {
    fn props(&self) -> Props;
    /// Its children, in order. An element whose children cannot be read has none.
    fn children(&self) -> Vec<Self>;
}

/// How much a snapshot holds.
#[derive(Clone, Copy, Debug)]
pub struct Caps {
    pub elements: usize,
    pub depth: usize,
    pub chars: usize,
    pub name_chars: usize,
    pub value_chars: usize,
}

impl Default for Caps {
    fn default() -> Self {
        Self {
            elements: 300,
            depth: 12,
            chars: 30_000,
            name_chars: 120,
            value_chars: 300,
        }
    }
}

/// One element of a snapshot, as the core is sent it.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Entry {
    pub id: u32,
    pub depth: u32,
    pub role: &'static str,
    pub name: String,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub value: Option<String>,
    pub enabled: bool,
    pub focused: bool,
    pub password: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub state: Option<&'static str>,
    pub actions: Vec<&'static str>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub rect: Option<[i32; 4]>,
}

/// A look at a window: its entries, the elements behind them (entry `id` is `nodes[id - 1]`),
/// and whether some were left out.
pub struct Snapshot<N> {
    pub entries: Vec<Entry>,
    pub nodes: Vec<N>,
    pub truncated: bool,
}

/// The name of a control type, as the model reads it.
pub fn role_name(control_type: i32) -> &'static str {
    const ROLES: [&str; 41] = [
        "Button",
        "Calendar",
        "CheckBox",
        "ComboBox",
        "Edit",
        "Hyperlink",
        "Image",
        "ListItem",
        "List",
        "Menu",
        "MenuBar",
        "MenuItem",
        "ProgressBar",
        "RadioButton",
        "ScrollBar",
        "Slider",
        "Spinner",
        "StatusBar",
        "Tab",
        "TabItem",
        "Text",
        "ToolBar",
        "ToolTip",
        "Tree",
        "TreeItem",
        "Custom",
        "Group",
        "Thumb",
        "DataGrid",
        "DataItem",
        "Document",
        "SplitButton",
        "Window",
        "Pane",
        "Header",
        "HeaderItem",
        "Table",
        "TitleBar",
        "Separator",
        "SemanticZoom",
        "AppBar",
    ];
    usize::try_from(control_type - 50_000)
        .ok()
        .and_then(|index| ROLES.get(index).copied())
        .unwrap_or("Custom")
}

/// Whether an element is only there to hold others: left out, its children kept.
fn is_filler(props: &Props, role: &str) -> bool {
    matches!(
        role,
        "Pane" | "Group" | "Custom" | "Image" | "Separator" | "Text"
    ) && props.name.trim().is_empty()
        && props.actions.is_empty()
        && props.value.as_deref().is_none_or(str::is_empty)
        && !props.focused
}

/// The snapshot of the tree under `root`. `out_of_time` is asked between elements: once it says
/// so, the walk stops where it is, and the snapshot says it was cut.
pub fn build<N: UiNode>(root: N, caps: Caps, out_of_time: impl Fn() -> bool) -> Snapshot<N> {
    // (path in the tree, depth among the kept, entry without its id, the node)
    let mut kept: Vec<(Vec<u32>, Entry, N)> = Vec::new();
    let mut queue: VecDeque<(N, Vec<u32>, u32, usize)> = VecDeque::new();
    queue.push_back((root, Vec::new(), 0, 0));
    let mut chars = 0usize;
    let mut truncated = false;
    while let Some((node, path, depth, level)) = queue.pop_front() {
        if kept.len() >= caps.elements || out_of_time() {
            truncated = true;
            break;
        }
        let props = node.props();
        if props.offscreen && level > 0 {
            continue;
        }
        let role = role_name(props.control_type);
        let filler = level > 0 && is_filler(&props, role);
        let next_depth = if filler { depth } else { depth + 1 };
        let children = node.children();
        if level < caps.depth {
            for (index, child) in children.into_iter().enumerate() {
                let mut child_path = path.clone();
                child_path.push(u32::try_from(index).unwrap_or(u32::MAX));
                queue.push_back((child, child_path, next_depth, level + 1));
            }
        } else if !children.is_empty() {
            truncated = true;
        }
        if filler {
            continue;
        }
        let name = clean(&props.name, caps.name_chars);
        let value = if props.password {
            None
        } else {
            props
                .value
                .as_deref()
                .map(|value| clean(value, caps.value_chars))
                .filter(|value| !value.is_empty())
        };
        let size = name.chars().count() + value.as_deref().map_or(0, |v| v.chars().count());
        if chars + size > caps.chars {
            truncated = true;
            break;
        }
        chars += size;
        let entry = Entry {
            id: 0,
            depth,
            role,
            name,
            value,
            enabled: props.enabled,
            focused: props.focused,
            password: props.password,
            state: props.state,
            actions: props.actions.clone(),
            rect: props.rect,
        };
        kept.push((path, entry, node));
    }
    kept.sort_by(|a, b| a.0.cmp(&b.0));
    let mut entries = Vec::with_capacity(kept.len());
    let mut nodes = Vec::with_capacity(kept.len());
    for (index, (_, mut entry, node)) in kept.into_iter().enumerate() {
        entry.id = u32::try_from(index + 1).unwrap_or(u32::MAX);
        entries.push(entry);
        nodes.push(node);
    }
    Snapshot {
        entries,
        nodes,
        truncated,
    }
}

#[cfg(test)]
pub(crate) mod tests {
    use super::*;
    use serde_json::json;
    use std::cell::Cell;

    /// A tree of elements for the tests.
    #[derive(Clone, Debug)]
    pub struct Fake {
        pub props: Props,
        pub children: Vec<Fake>,
    }

    impl UiNode for Fake {
        fn props(&self) -> Props {
            self.props.clone()
        }
        fn children(&self) -> Vec<Self> {
            self.children.clone()
        }
    }

    pub fn node(control_type: i32, name: &str, children: Vec<Fake>) -> Fake {
        Fake {
            props: Props {
                control_type,
                name: name.to_owned(),
                enabled: true,
                actions: if control_type == BUTTON {
                    vec!["invoke"]
                } else {
                    Vec::new()
                },
                ..Props::default()
            },
            children,
        }
    }

    pub const BUTTON: i32 = 50_000;
    pub const EDIT: i32 = 50_004;
    pub const TEXT: i32 = 50_020;
    pub const PANE: i32 = 50_033;
    pub const WINDOW: i32 = 50_032;

    pub fn calculator() -> Fake {
        let mut password = node(EDIT, "密碼", vec![]);
        password.props.password = true;
        password.props.value = Some("hunter2".into());
        password.props.actions = vec!["value"];
        let mut display = node(TEXT, "顯示為 0", vec![]);
        display.props.value = Some("0".into());
        let mut hidden = node(BUTTON, "hidden", vec![node(BUTTON, "under hidden", vec![])]);
        hidden.props.offscreen = true;
        node(
            WINDOW,
            "計算機",
            vec![
                display,
                node(
                    PANE,
                    "",
                    vec![node(BUTTON, "七", vec![]), node(BUTTON, "八", vec![])],
                ),
                password,
                hidden,
            ],
        )
    }

    fn never() -> bool {
        false
    }

    #[test]
    fn a_snapshot_lists_the_elements_in_the_order_of_the_tree_with_ids() {
        let shot = build(calculator(), Caps::default(), never);
        let rows: Vec<(u32, u32, &str, &str)> = shot
            .entries
            .iter()
            .map(|e| (e.id, e.depth, e.role, e.name.as_str()))
            .collect();
        // the nameless pane is left out, its buttons kept at its depth
        assert_eq!(
            rows,
            [
                (1, 0, "Window", "計算機"),
                (2, 1, "Text", "顯示為 0"),
                (3, 1, "Button", "七"),
                (4, 1, "Button", "八"),
                (5, 1, "Edit", "密碼"),
            ]
        );
        assert_eq!(shot.nodes[2].props.name, "七");
        assert!(!shot.truncated);
    }

    #[test]
    fn a_password_field_is_marked_and_never_has_a_value() {
        let shot = build(calculator(), Caps::default(), never);
        let password = shot.entries.iter().find(|e| e.password).unwrap();
        assert_eq!(password.value, None);
        let sent = serde_json::to_string(&shot.entries).unwrap();
        assert!(!sent.contains("hunter2"), "{sent}");
        assert_eq!(shot.entries[1].value.as_deref(), Some("0"));
    }

    #[test]
    fn what_is_off_the_screen_is_left_out_with_what_is_under_it() {
        let shot = build(calculator(), Caps::default(), never);
        assert!(shot.entries.iter().all(|e| !e.name.contains("hidden")));
    }

    #[test]
    fn the_caps_keep_the_structure_first_and_say_the_snapshot_was_cut() {
        let rows = (0..50)
            .map(|i| node(BUTTON, &format!("b{i}"), vec![node(BUTTON, "deep", vec![])]))
            .collect();
        let caps = Caps {
            elements: 20,
            ..Caps::default()
        };
        let shot = build(node(WINDOW, "w", rows), caps, never);
        assert_eq!(shot.entries.len(), 20);
        assert!(shot.truncated);
        // breadth first: the first level fills the room before any "deep" one
        assert!(shot.entries.iter().all(|e| e.name != "deep"));

        let mut chain = node(BUTTON, "leaf", vec![]);
        for i in 0..30 {
            chain = node(BUTTON, &format!("level {i}"), vec![chain]);
        }
        let caps = Caps {
            depth: 5,
            ..Caps::default()
        };
        let shot = build(chain, caps, never);
        assert_eq!(shot.entries.len(), 6);
        assert!(shot.truncated);
    }

    #[test]
    fn names_and_values_are_cut_and_the_characters_are_capped() {
        let long = "字".repeat(500);
        let mut box_ = node(EDIT, &long, vec![]);
        box_.props.value = Some(format!("{long}\nline"));
        let caps = Caps::default();
        let shot = build(node(WINDOW, "w", vec![box_]), caps, never);
        let entry = &shot.entries[1];
        assert_eq!(entry.name.chars().count(), caps.name_chars);
        assert_eq!(
            entry.value.as_deref().unwrap().chars().count(),
            caps.value_chars
        );
        let many = (0..100).map(|_| node(BUTTON, &long, vec![])).collect();
        let caps = Caps {
            chars: 1_000,
            ..Caps::default()
        };
        let shot = build(node(WINDOW, "w", many), caps, never);
        assert!(shot.truncated);
        let total: usize = shot.entries.iter().map(|e| e.name.chars().count()).sum();
        assert!(total <= 1_000);
    }

    #[test]
    fn a_walk_out_of_time_stops_where_it_is() {
        let asked = Cell::new(0);
        let shot = build(calculator(), Caps::default(), || {
            asked.set(asked.get() + 1);
            asked.get() > 2
        });
        assert_eq!(shot.entries.len(), 2);
        assert!(shot.truncated);
    }

    #[test]
    fn the_control_types_have_their_names() {
        assert_eq!(role_name(50_000), "Button");
        assert_eq!(role_name(50_004), "Edit");
        assert_eq!(role_name(50_032), "Window");
        assert_eq!(role_name(50_040), "AppBar");
        assert_eq!(role_name(49_999), "Custom");
        assert_eq!(role_name(60_000), "Custom");
    }

    #[test]
    fn an_entry_is_sent_in_the_shape_the_core_reads() {
        let shot = build(calculator(), Caps::default(), never);
        assert_eq!(
            serde_json::to_value(&shot.entries[2]).unwrap(),
            json!({"id": 3, "depth": 1, "role": "Button", "name": "七", "enabled": true,
                   "focused": false, "password": false, "actions": ["invoke"]})
        );
    }
}
