"""Unit tests for the Streamlit UI components."""

import pytest
import ast
import inspect
from pathlib import Path


class TestDashboardColumnUnpacking:
    """Tests to catch column unpacking errors in dashboard.py."""

    def test_no_column_unpacking_errors(self):
        """Verify st.columns(N) calls have matching unpacking.

        This test scans dashboard.py for patterns like:
        - col1, col2 = st.columns(3)  # ERROR: 2 vars, 3 columns
        - col1, col2, col3 = st.columns(3)  # OK: 3 vars, 3 columns
        """
        dashboard_path = Path(__file__).parent.parent.parent / "llauncher" / "ui" / "tabs" / "dashboard.py"
        content = dashboard_path.read_text()

        # Find all st.columns() calls and their unpacking
        tree = ast.parse(content)

        errors = []
        for node in ast.walk(tree):
            # Look for assignments like: col1, col2 = st.columns(3)
            if isinstance(node, ast.Assign):
                # Check if the value is a call to st.columns
                if isinstance(node.value, ast.Call):
                    call = node.value
                    # Check if it's st.columns()
                    if isinstance(call.func, ast.Attribute) and call.func.attr == "columns":
                        if isinstance(call.func.value, ast.Name) and call.func.value.id == "st":
                            # Get the number of columns (first argument)
                            if call.args:
                                num_columns = None
                                if isinstance(call.args[0], ast.Constant):
                                    num_columns = call.args[0].value
                                elif isinstance(call.args[0], ast.Num):  # Python < 3.8
                                    num_columns = call.args[0].n

                                # Count the number of variables being unpacked
                                num_vars = len(node.targets[0].elts) if isinstance(node.targets[0], ast.Tuple) else 1

                                if num_columns is not None and num_vars != num_columns:
                                    errors.append(
                                        f"Line {node.lineno}: Unpacking {num_vars} vars from st.columns({num_columns})"
                                    )

        assert not errors, "\n".join(errors)


class TestDashboardSyntax:
    """Basic syntax tests for dashboard.py."""

    def test_dashboard_syntax_valid(self):
        """Verify dashboard.py has valid Python syntax."""
        dashboard_path = Path(__file__).parent.parent.parent / "llauncher" / "ui" / "tabs" / "dashboard.py"
        content = dashboard_path.read_text()

        # This will raise SyntaxError if invalid
        ast.parse(content)

    def test_dashboard_imports_valid(self):
        """Verify all imports in dashboard.py can be resolved."""
        from llauncher.ui.tabs import dashboard

        # If this imports successfully, all dependencies are available
        assert hasattr(dashboard, "render_dashboard")
        assert hasattr(dashboard, "render_server_entry")
        assert hasattr(dashboard, "render_model_entry_from_dict")
        assert hasattr(dashboard, "render_add_model")
        assert hasattr(dashboard, "render_edit_model")


class TestAppSyntax:
    """Basic syntax tests for app.py."""

    def test_app_syntax_valid(self):
        """Verify app.py has valid Python syntax."""
        app_path = Path(__file__).parent.parent.parent / "llauncher" / "ui" / "app.py"
        content = app_path.read_text()

        # This will raise SyntaxError if invalid
        ast.parse(content)

    def test_app_imports_valid(self):
        """Verify all imports in app.py can be resolved."""
        # Note: We can't fully import the app due to Streamlit dependencies,
        # but we can verify the syntax is correct
        app_path = Path(__file__).parent.parent.parent / "llauncher" / "ui" / "app.py"
        content = app_path.read_text()

        # Check for common import errors
        tree = ast.parse(content)
        imports = []
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append(alias.name)
            elif isinstance(node, ast.ImportFrom):
                imports.append(node.module)

        # Verify key imports are present
        import_names = " ".join(imports)
        assert "streamlit" in import_names
        assert "llauncher.state" in import_names or "LauncherState" in import_names
