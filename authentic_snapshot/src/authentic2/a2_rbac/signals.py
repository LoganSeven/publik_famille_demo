from django import dispatch

# update role parenting transitive closure when role parenting is deleted
# these signals expect one argument: instance
post_soft_create = dispatch.Signal()
post_soft_delete = dispatch.Signal()
