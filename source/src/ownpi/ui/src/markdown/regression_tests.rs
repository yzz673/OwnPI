use super::*;
use crate::history_cell::{AgentMarkdownHistoryCell, HistoryCell, TypedThreadItemHistoryCell};
use crate::terminal_hyperlinks::HyperlinkLine;

fn text(lines: &[Line<'static>]) -> String {
    lines
        .iter()
        .map(Line::to_string)
        .collect::<Vec<_>>()
        .join("\n")
}

fn agent(source: &str, width: usize) -> Vec<HyperlinkLine> {
    AgentMarkdownHistoryCell::new(source.into(), ThemePalette::default())
        .with_cwd(Some("/workspace".into()))
        .display_hyperlink_lines(width)
}

fn destinations(lines: &[HyperlinkLine]) -> Vec<&str> {
    lines
        .iter()
        .flat_map(|line| line.hyperlinks.iter().map(|link| link.destination.as_str()))
        .collect()
}

fn bold_text(lines: &[Line<'static>]) -> String {
    lines
        .iter()
        .flat_map(|line| &line.spans)
        .filter(|span| span.style.add_modifier.contains(Modifier::BOLD))
        .map(|span| span.content.as_ref())
        .collect()
}

#[test]
fn cjk_punctuation_strong_preserves_exact_text_and_limits_bold_to_label() {
    for punctuation in ["：", "，", "。", "！", "？", "；", "）", "”", "】"] {
        let label = format!("一句话总结{punctuation}");
        let source = format!("**{label}**先定义测试时目标被试数据可用到什么程度。");
        for width in [20, 40, 80, 120] {
            let lines = render_markdown(&source, width, &ThemePalette::default());
            assert_eq!(
                text(&lines).replace('\n', ""),
                format!("{label}先定义测试时目标被试数据可用到什么程度。")
            );
            assert_eq!(bold_text(&lines), label, "{source} at {width}");
        }
    }
}

#[test]
fn cjk_strong_handles_repeated_labels_and_existing_inline_styles() {
    let source = "**总结：**正文；**方法：**EEG；**普通加粗**；**含 *斜体* 和 `代码`：**继续";
    let lines = render_markdown(source, 160, &ThemePalette::default());
    assert_eq!(
        text(&lines),
        "总结：正文；方法：EEG；普通加粗；含 斜体 和 代码：继续"
    );
    assert_eq!(bold_text(&lines), "总结：方法：普通加粗含 斜体 和 代码：");
    assert!(lines.iter().flat_map(|line| &line.spans).any(|span| {
        span.content == "斜体"
            && span
                .style
                .add_modifier
                .contains(Modifier::ITALIC | Modifier::BOLD)
    }));
}

#[test]
fn cjk_strong_preserves_links_references_and_table_cells() {
    let source = "**总结[论文][paper]：**正文[^1]\n\n| 结论 | 来源 |\n| --- | --- |\n| **方法：**对齐 | [**来源：**论文](https://example.org/**路径：**正文) |\n\n[paper]: https://example.org/paper?q=1&lang=zh\n\n[^1]: **注释：**说明";
    let lines = agent(source, 120);
    let visible = text(
        &lines
            .iter()
            .map(|line| line.line.clone())
            .collect::<Vec<_>>(),
    );
    assert!(visible.contains("总结论文：正文[^1]"), "{visible}");
    assert!(visible.contains("方法：对齐"), "{visible}");
    assert!(visible.contains("来源：论文"), "{visible}");
    assert!(visible.contains("[^1]: 注释：说明"), "{visible}");
    assert!(destinations(&lines).contains(&"https://example.org/paper?q=1&lang=zh"));
    let expected = links::destination("https://example.org/**路径：**正文", None).unwrap();
    assert!(
        destinations(&lines).contains(&expected.as_str()),
        "{lines:?}"
    );
    let bold = bold_text(
        &lines
            .iter()
            .map(|line| line.line.clone())
            .collect::<Vec<_>>(),
    );
    for label in ["总结论文：", "方法：", "来源：", "注释："] {
        assert!(bold.contains(label), "{bold}");
    }
}

#[test]
fn cjk_strong_leaves_literal_code_escaped_and_incomplete_delimiters_alone() {
    for (source, expected) in [
        ("`**总结：**正文`", "**总结：**正文"),
        (r"\*\*总结：\*\*正文", "**总结：**正文"),
        (r"\**总结：**正文", "**总结：**正文"),
        ("**总结：", "**总结："),
        ("**总结：*正文", "**总结：*正文"),
        ("**Summary:**text", "**Summary:**text"),
        ("***总结：***正文", "***总结：***正文"),
        ("** 总结：**正文", "** 总结：**正文"),
    ] {
        let lines = render_markdown(source, 120, &ThemePalette::default());
        assert_eq!(text(&lines), expected, "{source}");
        assert_eq!(bold_text(&lines), "", "{source}");
    }
    for source in ["```text\n**总结：**正文\n```", "    **总结：**正文"] {
        let lines = render_markdown(source, 120, &ThemePalette::default());
        assert!(text(&lines).contains("**总结：**正文"), "{lines:?}");
    }
}

#[test]
fn cjk_strong_never_pairs_across_blocks_or_link_boundaries() {
    for source in [
        "**开头\n\n结尾：**正文",
        "| 第一 | 第二 |\n| --- | --- |\n| **开头 | 结尾：**正文 |",
        "[**开头](https://example.org)结尾：**正文",
        "**开头[结尾：**正文](https://example.org)",
    ] {
        let lines = render_markdown(source, 120, &ThemePalette::default());
        assert!(text(&lines).contains("**开头"), "{source}: {lines:?}");
        assert!(text(&lines).contains("结尾：**正文"), "{source}: {lines:?}");
    }
}

#[test]
fn cjk_strong_streaming_prefixes_preserve_content_without_panics() {
    let source = "**一句话总结：**先定义数据范围。";
    for end in source.char_indices().map(|(i, _)| i).chain([source.len()]) {
        let prefix = &source[..end];
        let lines = render_markdown(prefix, 120, &ThemePalette::default());
        // Both complete and not-yet-complete delimiter pairs retain all prose.
        // A lone '*' is a CommonMark list marker, not an incomplete label yet.
        if end > 2 {
            assert_eq!(text(&lines).replace('*', ""), prefix.replace('*', ""));
        }
    }
}

#[test]
fn cjk_strong_preserving_breaks_keeps_newlines_and_style() {
    let lines = render_markdown_hyperlinks_preserving_breaks(
        "**总结：**正文\n第二行 [来源](https://example.org/paper)",
        120,
        &ThemePalette::default(),
        None,
    );
    assert!(destinations(&lines).contains(&"https://example.org/paper"));
    let lines = visible_lines(lines);
    assert_eq!(text(&lines), "总结：正文\n第二行 来源");
    assert_eq!(bold_text(&lines), "总结：");
}

#[test]
fn named_and_reference_links_keep_distinct_targets_for_identical_labels() {
    let source="[论文](https://example.org/first) [**论文**][second]\n\n[second]: https://example.org/second";
    for width in [20, 40, 80, 120] {
        let lines = agent(source, width);
        let targets = destinations(&lines);
        assert!(targets.contains(&"https://example.org/first"), "{lines:?}");
        assert!(targets.contains(&"https://example.org/second"), "{lines:?}");
        for line in &lines {
            for link in &line.hyperlinks {
                assert!(link.columns.end <= line.line.width());
            }
        }
    }
}

#[test]
fn wrapped_cjk_link_and_table_links_keep_full_destinations() {
    for width in [20, 40, 80, 120] {
        let source="[这是一段用于检查跨行保留链接身份的很长中文标题](https://example.org/long?x=1&y=2)\n\n| 来源 | 协议 |\n| --- | --- |\n| [论文](https://example.org/table) | `LOSO` |";
        let lines = agent(source, width);
        assert!(destinations(&lines).contains(&"https://example.org/long?x=1&y=2"));
        assert!(
            destinations(&lines).contains(&"https://example.org/table"),
            "{width}: {lines:?}"
        );
        assert!(lines.iter().all(|line| line.line.width() <= width));
    }
}

#[test]
fn review_summary_why_fix_and_assessment_keep_link_destinations() {
    for bound in [false, true] {
        for width in [20, 40, 80, 120] {
            let mut value = serde_json::json!({"schema_version":"ownpi.tui.item.v1","kind":"review_cycle",
            "item_id":"md-review","visibility":"user","status":"completed","payload":{
                "phase":"feedback_received","outcome":"findings","feedback_format":"structured_v2",
                "overall_assessment":"See [总体](https://example.org/overall)",
                "findings":[{"severity":"P2","title":"Source mapping","description":"[原因](https://example.org/why)",
                    "remediation":"[修复](https://example.org/fix)","evidence_refs":["https://example.org/evidence"]}]}});
            if bound {
                value["audit_context"] = serde_json::json!({"audit_id":"md-review","run_id":"run",
            "role":"reviewer","stage":"test","attempt":1,"review_round":1});
            }
            let cell = TypedThreadItemHistoryCell::new(
                crate::display_item::DisplayItem::parse_value(&value).unwrap(),
                ThemePalette::default(),
            );
            let lines = cell.transcript_hyperlink_lines(width);
            for target in ["why", "fix", "overall", "evidence"] {
                assert!(
                    destinations(&lines)
                        .contains(&format!("https://example.org/{target}").as_str()),
                    "{bound}/{width}/{target}: {lines:?}"
                );
            }
            value["payload"] = serde_json::json!({"phase":"feedback_received","outcome":"submitted",
            "summary":"[正文](https://example.org/body) and https://example.org/bare"});
            let cell = TypedThreadItemHistoryCell::new(
                crate::display_item::DisplayItem::parse_value(&value).unwrap(),
                ThemePalette::default(),
            );
            let lines = cell.transcript_hyperlink_lines(width);
            for url in ["https://example.org/body", "https://example.org/bare"] {
                assert!(
                    destinations(&lines).contains(&url),
                    "{bound}/{width}: {lines:?}"
                );
            }
        }
    }
}

#[test]
fn local_link_code_label_is_suppressed_and_target_is_resolved() {
    let lines = agent(
        "[`code_label`](docs/code.md:12) and [**报告**](/workspace/docs/report.md)",
        80,
    );
    let visible = text(&lines.iter().map(|l| l.line.clone()).collect::<Vec<_>>());
    assert!(visible.contains("docs/code.md:12"));
    assert!(
        !visible.contains("code_label") && !visible.contains("报告"),
        "{visible}"
    );
    assert!(
        destinations(&lines).contains(&"file:///workspace/docs/code.md#L12"),
        "{lines:?}"
    );
    assert!(destinations(&lines).contains(&"file:///workspace/docs/report.md"));
}

#[test]
fn unsafe_link_schemes_never_become_terminal_hyperlinks() {
    let lines=agent("[bad](javascript:alert%281%29) [data](data:text/plain,hello) [safe](https://example.org/safe)",80);
    assert!(destinations(&lines).contains(&"https://example.org/safe"));
    assert!(destinations(&lines)
        .iter()
        .all(|dest| !dest.starts_with("javascript:") && !dest.starts_with("data:")));
}

#[test]
fn local_encoded_paths_mail_and_protocol_relative_urls_keep_valid_targets() {
    let lines=agent("[本地](docs/中文%20报告.md#L7) [邮件](mailto:reader@example.org) [官网](//example.org/site)",80);
    for target in [
        "file:///workspace/docs/%E4%B8%AD%E6%96%87%20%E6%8A%A5%E5%91%8A.md#L7",
        "mailto:reader@example.org",
        "https://example.org/site",
    ] {
        assert!(
            destinations(&lines).contains(&target),
            "{target}: {lines:?}"
        );
    }
    assert_eq!(
        links::destination("report.md:12", Some("/workspace")).as_deref(),
        Some("file:///workspace/report.md#L12")
    );
    assert!(links::destination("https://example.org/\u{1b}bad", None).is_none());
}

#[test]
fn review_local_links_resolve_against_workspace_instead_of_launch_directory() {
    for bound in [false, true] {
        let mut value = serde_json::json!({"schema_version":"ownpi.tui.item.v1","kind":"review_cycle",
            "item_id":"cwd-review","visibility":"user","status":"completed","payload":{
                "phase":"feedback_received","outcome":"findings","feedback_format":"structured_v2",
                "findings":[{"severity":"P2","title":"Local source","evidence_refs":["docs/evidence.md:12"],
                    "description":"See [`local_label`](docs/report.md:8)","remediation":"Open [来源](docs/fix.md)"}]}});
        if bound {
            value["audit_context"] = serde_json::json!({"audit_id":"cwd","run_id":"run",
            "role":"reviewer","stage":"test","attempt":1,"review_round":1});
        }
        let cell = TypedThreadItemHistoryCell::new(
            crate::display_item::DisplayItem::parse_value(&value).unwrap(),
            ThemePalette::default(),
        )
        .with_cwd(Some("/explicit/workspace".into()));
        for width in [20, 40, 80, 120] {
            let lines = cell.transcript_hyperlink_lines(width);
            for target in [
                "file:///explicit/workspace/docs/evidence.md#L12",
                "file:///explicit/workspace/docs/report.md#L8",
                "file:///explicit/workspace/docs/fix.md",
            ] {
                assert!(
                    destinations(&lines).contains(&target),
                    "{bound}/{width}: {lines:?}"
                );
            }
        }
    }
}

#[test]
fn mixed_quote_and_list_containers_keep_source_order() {
    let lines=render_markdown("- Parent\n  > Quote inside item\n  >\n  > - Nested child\n\n> - Item in quote\n>   - Child in quote",80,&ThemePalette::default());
    let visible = text(&lines);
    for expected in [
        "  > Quote inside item",
        "  > • Nested child",
        "> • Item in quote",
        ">   • Child in quote",
    ] {
        assert!(
            visible.lines().any(|row| row == expected),
            "{expected}: {visible}"
        );
    }
}

#[test]
fn footnotes_keep_reference_and_definition_identity_and_continuations() {
    let source="结论[^来源]，重复[^来源]，另一条[^two]。\n\n[^来源]: **来源一**，见 [论文](https://example.org/footnote)。\n\n    这仍属于第一条脚注。\n\n[^two]: 第二条来源。";
    for width in [40, 80, 120] {
        let lines = render_markdown(source, width, &ThemePalette::default());
        let visible = text(&lines);
        assert_eq!(visible.matches("[^来源]").count(), 3, "{visible}");
        assert!(
            visible.contains("[^来源]: ") && visible.contains("[^two]: "),
            "{visible}"
        );
        let continuation = lines
            .iter()
            .find(|l| l.to_string().contains("这仍属于第一条脚注"))
            .unwrap()
            .to_string();
        assert!(continuation.starts_with(' '), "{visible}");
        assert!(destinations(&agent(source, width)).contains(&"https://example.org/footnote"));
    }
}

#[test]
fn nested_lists_and_parent_continuation_preserve_hierarchy() {
    let source =
        "1. Parent\n   - Child\n     - Grandchild\n\n   Parent continuation.\n\n2. Sibling";
    let lines = render_markdown(source, 80, &ThemePalette::default());
    let visible = text(&lines);
    for expected in [
        "1. Parent",
        "   • Child",
        "     • Grandchild",
        "   Parent continuation.",
        "2. Sibling",
    ] {
        assert!(
            visible.lines().any(|line| line == expected),
            "{expected}: {visible}"
        );
    }
}

#[test]
fn nested_tasks_and_wrapped_items_keep_hanging_indent() {
    let source="- [x] Parent\n  - [ ] Child 中文任务需要在窄屏保持层级以及换行缩进，不能变成顶层内容。\n- [ ] Sibling";
    for width in [20, 40, 80, 120] {
        let lines = render_markdown(source, width, &ThemePalette::default());
        let visible = text(&lines);
        assert!(visible.contains("  • [ ] Child"), "{visible}");
        assert!(lines.iter().all(|line| line.width() <= width));
        let child = visible
            .lines()
            .position(|line| line.contains("Child"))
            .unwrap();
        for row in visible
            .lines()
            .skip(child + 1)
            .take_while(|line| !line.starts_with("•"))
        {
            assert!(row.is_empty() || row.starts_with("    "), "{visible}");
        }
    }
}

#[test]
fn image_fallback_keeps_alt_and_target_even_without_alt() {
    let source = "![示意图](https://example.org/figure.png)\n\n![](images/empty-alt.png)";
    for width in [20, 40, 80, 120] {
        let lines = agent(source, width);
        let visible = text(&lines.iter().map(|l| l.line.clone()).collect::<Vec<_>>());
        assert!(visible.contains("示意图"));
        assert!(
            visible
                .replace('\n', "")
                .replace(' ', "")
                .contains("https://example.org/figure.png"),
            "{visible}"
        );
        assert!(visible.contains("empty-alt.png"), "{visible}");
        assert!(destinations(&lines).contains(&"https://example.org/figure.png"));
        assert!(destinations(&lines).contains(&"file:///workspace/images/empty-alt.png"));
        assert!(lines.iter().all(|line| line.line.width() <= width));
    }
}

#[test]
fn quoted_and_listed_code_keeps_container_prefix_and_parent_indent() {
    let source="> Quoted:\n>\n> ```python\n> print('quoted')\n> ```\n\n- Parent\n\n  ```python\n  print('listed')\n  ```\n\n  Still parent.";
    let lines = render_markdown(source, 80, &ThemePalette::default());
    let visible = text(&lines);
    assert!(
        visible.lines().any(|line| line == "> ``` python"),
        "{visible}"
    );
    assert!(
        visible.lines().any(|line| line == ">   print('quoted')"),
        "{visible}"
    );
    assert!(
        visible.lines().any(|line| line == "  ``` python"),
        "{visible}"
    );
    assert!(
        visible.lines().any(|line| line == "    print('listed')"),
        "{visible}"
    );
    assert!(
        visible.lines().any(|line| line == "  Still parent."),
        "{visible}"
    );
}

#[test]
fn long_code_wraps_without_losing_whitespace_or_cjk_content() {
    let code = format!("  prefix    {}    suffix", "中文".repeat(50));
    for width in [20, 40, 80, 120] {
        let lines = render_markdown(
            &format!("```text\n{code}\n```"),
            width,
            &ThemePalette::default(),
        );
        assert!(
            lines.iter().all(|line| line.width() <= width),
            "{width}: {lines:?}"
        );
        let actual = lines[1..lines.len() - 1]
            .iter()
            .map(|line| line.to_string().strip_prefix("  ").unwrap().to_string())
            .collect::<String>();
        assert_eq!(actual, code, "width {width}");
    }
}

#[test]
fn review_display_fields_preserve_semantic_leading_code_indentation() {
    for bound in [false, true] {
        for structured in [false, true] {
            let mut value = serde_json::json!({"schema_version":"ownpi.tui.item.v1","kind":"review_cycle",
            "item_id":"indented-review","visibility":"user","status":"completed","payload":{
                "phase":"feedback_received","outcome":"submitted","summary":"    summary_code()\n\nParagraph.",
                "overall_assessment":"    overall_code()"}});
            if bound {
                value["audit_context"] = serde_json::json!({"audit_id":"code","run_id":"run",
            "role":"reviewer","stage":"test","attempt":1,"review_round":1});
            }
            if structured {
                value["payload"]["feedback_format"] = serde_json::json!("structured_v2");
                value["payload"]["findings"] = serde_json::json!([{"severity":"P2","title":"Code",
                "description":"    why_code()","remediation":"    fix_code()"}]);
            }
            let item = crate::display_item::DisplayItem::parse_value(&value).unwrap();
            let cell = TypedThreadItemHistoryCell::new(item, ThemePalette::default());
            let visible = text(&cell.transcript_lines(80));
            let count = if structured { 3 } else { 2 };
            assert_eq!(
                visible.matches("``` code").count(),
                count,
                "{bound}/{structured}: {visible}"
            );
        }
    }
}

#[test]
fn ordinary_and_review_paragraph_gaps_are_preserved_without_trailing_spacers() {
    let source = "First paragraph.\n\nSecond paragraph.\n\n- one\n- two\n\nLast paragraph.";
    let lines = render_markdown(source, 80, &ThemePalette::default());
    let visible = text(&lines);
    assert!(
        visible.contains("First paragraph.\n\nSecond paragraph."),
        "{visible}"
    );
    assert!(visible.contains("• one\n• two"), "{visible}");
    assert!(!visible.ends_with('\n'));
}

#[test]
fn stream_and_committed_markdown_agree_and_copy_keeps_original_source() {
    let source="段落[^source]。\n\n- Parent\n  - [链接](https://example.org/a)\n\n[^source]: 来源。\n\n![图](https://example.org/image.png)";
    for width in [20, 40, 80, 120] {
        let theme = ThemePalette::default();
        let cell = AgentMarkdownHistoryCell::new(source.into(), theme.clone());
        assert_eq!(cell.copy_text().as_deref(), Some(source));
        assert_eq!(
            cell.display_lines(width)
                .iter()
                .map(Line::to_string)
                .collect::<Vec<_>>(),
            crate::history_cell::render_agent_live_response_with_cwd(source, width, &theme, None)
                .iter()
                .map(Line::to_string)
                .collect::<Vec<_>>()
        );
    }
}

#[test]
fn unmatched_citations_are_detail_only_without_changing_raw_or_copy_text() {
    let source = "结论citeturn1view0turn2search1。\n\n| 方法 | 结果 |\n| --- | --- |\n| EEG | 有限citeturn3view0 |\n\n[真实来源](https://example.org/paper)";
    for width in [20, 40, 120] {
        let theme = ThemePalette::default();
        let cell = AgentMarkdownHistoryCell::new(source.into(), theme.clone());
        let lines = cell.display_hyperlink_lines(width);
        let visible = text(&crate::terminal_hyperlinks::visible_lines(lines.clone()));
        assert!(
            !visible.contains('\u{e200}') && !visible.contains("引用未关联"),
            "{visible}"
        );
        assert!(
            visible.contains("结论。") && visible.contains("有限"),
            "{visible}"
        );
        assert!(
            !visible.contains("[1]") && !visible.contains("turn1view0"),
            "{visible}"
        );
        let details = text(&cell.transcript_lines(width));
        let compact: String = details.chars().filter(|c| !c.is_whitespace()).collect();
        for id in ["turn1view0", "turn2search1", "turn3view0"] {
            assert!(compact.contains(id), "{details}");
        }
        assert!(compact.contains("引用ID未匹配"), "{details}");
        assert!(destinations(&lines).contains(&"https://example.org/paper"));
        assert_eq!(cell.copy_text().as_deref(), Some(source));
        assert_eq!(cell.raw_text().as_deref(), Some(source));
        assert_eq!(
            text(&cell.display_lines(width)),
            text(&crate::history_cell::render_agent_live_response_with_cwd(
                source, width, &theme, None
            ))
        );
    }
}

#[test]
fn citation_examples_in_code_stay_literal_and_explicit_links_stay_bound() {
    let literal = "citeturn1view0";
    for source in [
        format!("`{literal}`"),
        format!("```text\n{literal}\n```"),
        format!("    {literal}\n"),
    ] {
        let visible = text(&render_markdown(&source, 100, &ThemePalette::default()));
        assert!(visible.contains(literal), "{visible}");
    }
    let lines = agent(&format!("[{literal}](https://example.org/exact)"), 80);
    let visible = text(&crate::terminal_hyperlinks::visible_lines(lines.clone()));
    assert!(visible.contains("[1]") && !visible.contains("未关联"));
    assert!(visible.contains("https://example.org/exact"));
    assert!(destinations(&lines).contains(&"https://example.org/exact"));
}

#[test]
fn numbered_reference_links_survive_an_unclosed_code_fence() {
    let source = "Claim[citeturn7search0](https://example.org/paper)\n\n```python\nunfinished";
    let cell = AgentMarkdownHistoryCell::new(source.into(), ThemePalette::default());
    let lines = cell.display_hyperlink_lines(100);
    let visible = text(&crate::terminal_hyperlinks::visible_lines(lines.clone()));
    assert!(visible.contains("Claim[1]"), "{visible}");
    assert!(
        visible.contains("[1] https://example.org/paper"),
        "{visible}"
    );
    assert!(
        destinations(&lines)
            .iter()
            .filter(|url| **url == "https://example.org/paper")
            .count()
            >= 2
    );
    assert_eq!(cell.raw_text().as_deref(), Some(source));
}

#[test]
fn actual_speech_answer_preserves_bibliography_and_moves_all_six_ids_to_details() {
    let source = include_str!("../../tests/fixtures/speech_answer_citations.md");
    for width in [20, 40, 80, 120] {
        let cell = AgentMarkdownHistoryCell::new(source.into(), ThemePalette::default());
        let lines = cell.display_hyperlink_lines(width);
        assert!(lines.iter().all(|line| line.line.width() <= width));
        let visible = text(&crate::terminal_hyperlinks::visible_lines(lines));
        let compact: String = visible.chars().filter(|c| !c.is_whitespace()).collect();
        assert!(
            !visible.contains("turn527410")
                && !visible.contains("未保存")
                && !visible.contains("[1]"),
            "{visible}"
        );
        for bibliography in [
            "10.1038/s41467-025-63825-0",
            "2610.20954v1",
            "2025.emnlp-main.289",
            "AdityaSingh",
            "BlessingIsoyizaAdeika",
            "CongchiYin",
        ] {
            assert!(compact.contains(bibliography), "{width}: {bibliography}");
        }
        let details = cell.transcript_hyperlink_lines(width);
        assert!(details.iter().all(|line| line.line.width() <= width));
        let detailed: String = details
            .iter()
            .map(|line| line.line.to_string())
            .collect::<Vec<_>>()
            .join("")
            .chars()
            .filter(|c| !c.is_whitespace())
            .collect();
        for id in [
            "turn527410search14",
            "turn527410search0",
            "turn527410academia12",
            "turn527410search2",
            "turn527410search1",
            "turn527410search13",
        ] {
            assert!(
                detailed.contains(&format!("引用ID未匹配：{id}")),
                "{detailed}"
            );
        }
        assert_eq!(cell.copy_text().as_deref(), Some(source));
        assert_eq!(cell.raw_text().as_deref(), Some(source));
    }
}
