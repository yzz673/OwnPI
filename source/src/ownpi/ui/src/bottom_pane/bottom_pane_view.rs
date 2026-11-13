//! Shared active-view ownership for bottom-pane modal surfaces.

use std::any::Any;
use std::fmt;

use crate::option_picker::OptionPickerState;

use super::{
    ApprovalOverlayState, CommandBrowserViewState, CommandDocumentViewState,
    HumanQuestionViewState, SelectionViewState, SkillsToggleViewState,
};

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum BottomPaneViewKind {
    Selection,
    OptionPicker,
    CommandDocument,
    CommandBrowser,
    Skills,
    HumanQuestion,
    Permission,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum ViewCompletion {
    Accepted,
    Cancelled,
}

/// Every modal owns its state inside the stack. Concrete views can add richer
/// input/render behavior without adding parallel `Option` fields to the pane.
pub(crate) trait BottomPaneView: fmt::Debug + Any {
    fn view_kind(&self) -> BottomPaneViewKind;
    fn as_any(&self) -> &dyn Any;
    fn as_any_mut(&mut self) -> &mut dyn Any;
    fn into_any(self: Box<Self>) -> Box<dyn Any>;

    fn requires_human_action(&self) -> bool {
        matches!(
            self.view_kind(),
            BottomPaneViewKind::HumanQuestion | BottomPaneViewKind::Permission
        )
    }

    fn dismiss_after_child_accept(&self) -> bool {
        false
    }
}

macro_rules! impl_view {
    ($type:ty, $kind:expr) => {
        impl BottomPaneView for $type {
            fn view_kind(&self) -> BottomPaneViewKind {
                $kind
            }

            fn as_any(&self) -> &dyn Any {
                self
            }

            fn as_any_mut(&mut self) -> &mut dyn Any {
                self
            }

            fn into_any(self: Box<Self>) -> Box<dyn Any> {
                self
            }
        }
    };
}

impl_view!(SelectionViewState, BottomPaneViewKind::Selection);
impl_view!(OptionPickerState, BottomPaneViewKind::OptionPicker);
impl_view!(
    CommandDocumentViewState,
    BottomPaneViewKind::CommandDocument
);
impl_view!(CommandBrowserViewState, BottomPaneViewKind::CommandBrowser);
impl_view!(SkillsToggleViewState, BottomPaneViewKind::Skills);
impl_view!(HumanQuestionViewState, BottomPaneViewKind::HumanQuestion);

impl BottomPaneView for ApprovalOverlayState {
    fn view_kind(&self) -> BottomPaneViewKind {
        BottomPaneViewKind::Permission
    }

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn as_any_mut(&mut self) -> &mut dyn Any {
        self
    }

    fn into_any(self: Box<Self>) -> Box<dyn Any> {
        self
    }
}

#[cfg(test)]
#[derive(Debug)]
pub(crate) struct InteractionViewMarker(pub(crate) BottomPaneViewKind);

#[cfg(test)]
impl BottomPaneView for InteractionViewMarker {
    fn view_kind(&self) -> BottomPaneViewKind {
        self.0
    }

    fn as_any(&self) -> &dyn Any {
        self
    }

    fn as_any_mut(&mut self) -> &mut dyn Any {
        self
    }

    fn into_any(self: Box<Self>) -> Box<dyn Any> {
        self
    }
}

#[derive(Default)]
pub(crate) struct BottomPaneViewStack {
    views: Vec<Box<dyn BottomPaneView>>,
}

impl fmt::Debug for BottomPaneViewStack {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter
            .debug_list()
            .entries(self.views.iter().map(|view| view.view_kind()))
            .finish()
    }
}

impl BottomPaneViewStack {
    pub(crate) fn active(&self) -> Option<BottomPaneViewKind> {
        self.views.last().map(|view| view.view_kind())
    }

    pub(crate) fn active_as<T: Any>(&self) -> Option<&T> {
        self.views.last()?.as_any().downcast_ref::<T>()
    }

    pub(crate) fn active_as_mut<T: Any>(&mut self) -> Option<&mut T> {
        self.views.last_mut()?.as_any_mut().downcast_mut::<T>()
    }

    pub(crate) fn replace<T: BottomPaneView>(&mut self, view: T) {
        self.views.clear();
        self.views.push(Box::new(view));
    }

    pub(crate) fn push<T: BottomPaneView>(&mut self, view: T) {
        self.views.push(Box::new(view));
    }

    pub(crate) fn pop(&mut self, completion: ViewCompletion) {
        if self.views.pop().is_none() {
            return;
        }
        if completion == ViewCompletion::Accepted {
            while self
                .views
                .last()
                .is_some_and(|view| view.dismiss_after_child_accept())
            {
                self.views.pop();
            }
        }
    }

    pub(crate) fn take_active<T: Any>(&mut self) -> Option<T> {
        if !self
            .views
            .last()
            .is_some_and(|view| view.as_any().is::<T>())
        {
            return None;
        }
        self.views
            .pop()?
            .into_any()
            .downcast::<T>()
            .ok()
            .map(|view| *view)
    }

    pub(crate) fn clear(&mut self) {
        self.views.clear();
    }

    pub(crate) fn is_empty(&self) -> bool {
        self.views.is_empty()
    }

    pub(crate) fn requires_human_action(&self) -> bool {
        self.views
            .last()
            .is_some_and(|view| view.requires_human_action())
    }

    #[cfg(test)]
    pub(crate) fn depth(&self) -> usize {
        self.views.len()
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn replacing_modal_keeps_single_trait_object_owner() {
        let mut stack = BottomPaneViewStack::default();
        stack.replace(InteractionViewMarker(BottomPaneViewKind::HumanQuestion));
        assert_eq!(stack.active(), Some(BottomPaneViewKind::HumanQuestion));
        assert_eq!(stack.depth(), 1);
        stack.replace(InteractionViewMarker(BottomPaneViewKind::Permission));
        assert_eq!(stack.active(), Some(BottomPaneViewKind::Permission));
        assert_eq!(stack.depth(), 1);
    }

    #[test]
    fn concrete_state_round_trips_through_trait_object_stack() {
        let mut stack = BottomPaneViewStack::default();
        let view = CommandBrowserViewState::new(Vec::new());
        stack.replace(view);
        assert!(stack.active_as::<CommandBrowserViewState>().is_some());
        assert!(stack.take_active::<CommandBrowserViewState>().is_some());
        assert!(stack.is_empty());
    }

    #[test]
    fn nested_cancel_restores_parent_view() {
        let mut stack = BottomPaneViewStack::default();
        stack.replace(InteractionViewMarker(BottomPaneViewKind::Selection));
        stack.push(InteractionViewMarker(BottomPaneViewKind::Skills));
        assert_eq!(stack.depth(), 2);
        stack.pop(ViewCompletion::Cancelled);
        assert_eq!(stack.active(), Some(BottomPaneViewKind::Selection));
        assert_eq!(stack.depth(), 1);
    }

    #[test]
    fn only_interaction_views_require_human_action() {
        let mut stack = BottomPaneViewStack::default();
        stack.replace(InteractionViewMarker(BottomPaneViewKind::Selection));
        assert!(!stack.requires_human_action());
        stack.replace(InteractionViewMarker(BottomPaneViewKind::HumanQuestion));
        assert!(stack.requires_human_action());
    }
}
