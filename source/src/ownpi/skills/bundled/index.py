from __future__ import annotations

from ownpi.skills.bundled.academic_paper_writing import register_academic_paper_writing_skill
from ownpi.skills.bundled.ai_model_guide import register_ai_model_guide_skill
from ownpi.skills.bundled.build_fixer import register_build_fixer_skill
from ownpi.skills.bundled.bug_detective import register_bug_detective_skill
from ownpi.skills.bundled.checkpoint_manager import register_checkpoint_manager_skill
from ownpi.skills.bundled.citation_verification import register_citation_verification_skill
from ownpi.skills.bundled.code_review_excellence import register_code_review_excellence_skill
from ownpi.skills.bundled.daily_coding import register_daily_coding_skill
from ownpi.skills.bundled.doc import register_doc_skill
from ownpi.skills.bundled.doc_coauthoring import register_doc_coauthoring_skill
from ownpi.skills.bundled.frontend_design import register_frontend_design_skill
from ownpi.skills.bundled.feedback_authoring import register_feedback_authoring_skill
from ownpi.skills.bundled.git_workflow import register_git_workflow_skill
from ownpi.skills.bundled.latex_workflow import register_latex_workflow_skill
from ownpi.skills.bundled.mcp_integration import register_mcp_integration_skill
from ownpi.skills.bundled.paper_framework_figure import register_paper_framework_figure_skill
from ownpi.skills.bundled.publication_figure import register_publication_figure_skill
from ownpi.skills.bundled.research_data_availability import register_research_data_availability_skill
from ownpi.skills.bundled.planning_with_files import register_planning_with_files_skill
from ownpi.skills.bundled.post_acceptance import register_post_acceptance_skill
from ownpi.skills.bundled.pdf import register_pdf_skill
from ownpi.skills.bundled.results_analysis import register_results_analysis_skill
from ownpi.skills.bundled.results_report import register_results_report_skill
from ownpi.skills.bundled.session_wrap_up import register_session_wrap_up_skill
from ownpi.skills.bundled.ui_ux_pro_max import register_ui_ux_pro_max_skill
from ownpi.skills.bundled.uv_package_manager import register_uv_package_manager_skill
from ownpi.skills.bundled.verification_loop import register_verification_loop_skill
from ownpi.skills.bundled.webapp_testing import register_webapp_testing_skill


def register_all_bundled_skills() -> None:
    register_academic_paper_writing_skill()
    register_ai_model_guide_skill()
    register_build_fixer_skill()
    register_bug_detective_skill()
    register_checkpoint_manager_skill()
    register_citation_verification_skill()
    register_code_review_excellence_skill()
    register_daily_coding_skill()
    register_doc_skill()
    register_doc_coauthoring_skill()
    register_feedback_authoring_skill()
    register_frontend_design_skill()
    register_git_workflow_skill()
    register_latex_workflow_skill()
    register_mcp_integration_skill()
    register_paper_framework_figure_skill()
    register_publication_figure_skill()
    register_research_data_availability_skill()
    register_planning_with_files_skill()
    register_post_acceptance_skill()
    register_pdf_skill()
    register_results_analysis_skill()
    register_results_report_skill()
    register_session_wrap_up_skill()
    register_verification_loop_skill()
    register_ui_ux_pro_max_skill()
    register_uv_package_manager_skill()
    register_webapp_testing_skill()
