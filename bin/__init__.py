"""项目脚本包。

显式声明为常规包（而不是命名空间包），避免被 site-packages 里同名的残留
``bin`` 目录抢占——否则 ``python -m app.main`` 会报
``ModuleNotFoundError: No module named 'bin.ensure_local_models'``。
"""
