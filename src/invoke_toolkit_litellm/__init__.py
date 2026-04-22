"""invoke-toolkit-litellm - A set of invoke-toolkit tasks"""

__version__ = "0.1.0"
__author__ = "Nahuel Defossé"
__email__ = "author@example.com"

from invoke_toolkit.collections import ToolkitCollection

# Create a collection that auto-discovers all tasks from submodules in a flat structure.
# This means all @task decorated functions from all modules (tasks.py, utils.py, etc.)
# will be directly accessible under the collection name without the module prefix.
#
# Example usage:
#   intk invoke_toolkit_litellm.hello           # From tasks.py
#   intk invoke_toolkit_litellm.my_task         # From utils.py or any other module
#
# NOT:
#   intk invoke_toolkit_litellm.tasks.hello     # (old nested structure)
#   intk invoke_toolkit_litellm.utils.my_task
#
# If you prefer the nested structure (tasks grouped by module), replace the line below with:
#   collection.add_collections_from_namespace("invoke_toolkit_litellm")
# This will preserve the module names in the namespace.

collection = ToolkitCollection("invoke_toolkit_litellm")
collection.add_flat_tasks_from_namespace("invoke_toolkit_litellm")

__all__ = ["collection"]
