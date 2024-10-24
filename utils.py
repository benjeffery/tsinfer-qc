"""
QC functions for tsinfer trees
"""
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd
import seaborn as sns
import tskit
import numba


spec = [
    ("num_edges", numba.int64),
    ("sequence_length", numba.float64),
    ("edges_left", numba.float64[:]),
    ("edges_right", numba.float64[:]),
    ("edge_insertion_order", numba.int32[:]),
    ("edge_removal_order", numba.int32[:]),
    ("edge_insertion_index", numba.int64),
    ("edge_removal_index", numba.int64),
    ("interval", numba.float64[:]),
    ("in_range", numba.int64[:]),
    ("out_range", numba.int64[:]),
]


@numba.experimental.jitclass(spec)
class TreePosition:
    def __init__(
        self,
        num_edges,
        sequence_length,
        edges_left,
        edges_right,
        edge_insertion_order,
        edge_removal_order,
    ):
        self.num_edges = num_edges
        self.sequence_length = sequence_length
        self.edges_left = edges_left
        self.edges_right = edges_right
        self.edge_insertion_order = edge_insertion_order
        self.edge_removal_order = edge_removal_order
        self.edge_insertion_index = 0
        self.edge_removal_index = 0
        self.interval = np.zeros(2)
        self.in_range = np.zeros(2, dtype=np.int64)
        self.out_range = np.zeros(2, dtype=np.int64)

    def next(self):
        left = self.interval[1]
        j = self.in_range[1]
        k = self.out_range[1]
        self.in_range[0] = j
        self.out_range[0] = k
        M = self.num_edges
        edges_left = self.edges_left
        edges_right = self.edges_right
        out_order = self.edge_removal_order
        in_order = self.edge_insertion_order

        while k < M and edges_right[out_order[k]] == left:
            k += 1
        while j < M and edges_left[in_order[j]] == left:
            j += 1
        self.out_range[1] = k
        self.in_range[1] = j

        right = self.sequence_length
        if j < M:
            right = min(right, edges_left[in_order[j]])
        if k < M:
            right = min(right, edges_right[out_order[k]])
        self.interval[:] = [left, right]
        return j < M or left < self.sequence_length


# Helper function to make it easier to communicate with the numba class
def alloc_tree_position(ts):
    return TreePosition(
        num_edges=ts.num_edges,
        sequence_length=ts.sequence_length,
        edges_left=ts.edges_left,
        edges_right=ts.edges_right,
        edge_insertion_order=ts.indexes_edge_insertion_order,
        edge_removal_order=ts.indexes_edge_removal_order,
    )


@numba.njit
def _compute_per_tree_stats(
    tree_pos, num_trees, num_nodes, nodes_time, edges_parent, edges_child
):
    tbl = np.zeros(num_trees)
    num_internal_nodes = np.zeros(num_trees)
    max_arity = np.zeros(num_trees, dtype=np.int32)
    num_children = np.zeros(num_nodes, dtype=np.int32)
    nodes_with_arity = np.zeros(num_nodes, dtype=np.int32)

    current_tbl = 0
    tree_index = 0
    current_num_internal_nodes = 0
    current_max_arity = 0
    while tree_pos.next():
        for j in range(tree_pos.out_range[0], tree_pos.out_range[1]):
            e = tree_pos.edge_removal_order[j]
            p = edges_parent[e]
            nodes_with_arity[num_children[p]] -= 1
            if (
                num_children[p] == current_max_arity
                and nodes_with_arity[num_children[p]] == 1
            ):
                current_max_arity -= 1

            num_children[p] -= 1
            if num_children[p] == 0:
                current_num_internal_nodes -= 1
            else:
                nodes_with_arity[num_children[p]] += 1
            c = edges_child[e]
            branch_length = nodes_time[p] - nodes_time[c]
            current_tbl -= branch_length

        for j in range(tree_pos.in_range[0], tree_pos.in_range[1]):
            e = tree_pos.edge_insertion_order[j]
            p = edges_parent[e]
            if num_children[p] == 0:
                current_num_internal_nodes += 1
            else:
                nodes_with_arity[num_children[p]] -= 1
            num_children[p] += 1
            nodes_with_arity[num_children[p]] += 1
            if num_children[p] > current_max_arity:
                current_max_arity = num_children[p]
            c = edges_child[e]
            branch_length = nodes_time[p] - nodes_time[c]
            current_tbl += branch_length
        tbl[tree_index] = current_tbl
        num_internal_nodes[tree_index] = current_num_internal_nodes
        max_arity[tree_index] = current_max_arity
        tree_index += 1
        # print("tree", tree_index, nodes_with_arity)

    return tbl, num_internal_nodes, max_arity


def compute_per_tree_stats(ts):
    """
    Returns the per-tree statistics
    """
    tree_pos = alloc_tree_position(ts)
    return _compute_per_tree_stats(
        tree_pos,
        ts.num_trees,
        ts.num_nodes,
        ts.nodes_time,
        ts.edges_parent,
        ts.edges_child,
    )


class TreeInfo:
    """
    Class for storing tree information
    """

    def __init__(self, ts, chr):
        self.ts = ts
        self.chr = chr

        self.sites_num_mutations = np.bincount(
            self.ts.mutations_site, minlength=self.ts.num_sites
        )
        self.nodes_num_mutations = np.bincount(
            self.ts.mutations_node, minlength=self.ts.num_nodes
        )

    def summary(self):
        nodes_with_zero_muts = np.sum(self.nodes_num_mutations == 0)
        sites_with_zero_muts = np.sum(self.sites_num_mutations == 0)

        data = [
            ("samples", self.ts.num_samples),
            ("nodes", self.ts.num_nodes),
            ("mutations", self.ts.num_mutations),
            ("nodes_with_zero_muts", nodes_with_zero_muts),
            ("sites_with_zero_muts", sites_with_zero_muts),
            ("max_mutations_per_site", np.max(self.sites_num_mutations)),
            ("mean_mutations_per_site", np.mean(self.sites_num_mutations)),
            ("median_mutations_per_site", np.median(self.sites_num_mutations)),
            ("max_mutations_per_node", np.max(self.nodes_num_mutations)),
        ]
        df = pd.DataFrame(
            {"property": [d[0] for d in data], "value": [d[1] for d in data]}
        )
        return df.set_index("property")

    def _repr_html_(self):
        return self.summary()._repr_html_()

    @staticmethod
    @numba.njit
    def child_bounds(num_nodes, edges_left, edges_right, edges_child):
        num_edges = edges_left.shape[0]
        child_left = np.zeros(num_nodes, dtype=np.float64) + np.inf
        child_right = np.zeros(num_nodes, dtype=np.float64)

        for e in range(num_edges):
            u = edges_child[e]
            if edges_left[e] < child_left[u]:
                child_left[u] = edges_left[e]
            if edges_right[e] > child_right[u]:
                child_right[u] = edges_right[e]
        return child_left, child_right

    def mutations_data(self):
        # FIXME use tskit's impute mutations time
        ts = self.ts
        mutations_time = ts.mutations_time.copy()
        mutations_node = ts.mutations_node.copy()
        unknown = tskit.is_unknown_time(mutations_time)
        mutations_time[unknown] = self.ts.nodes_time[mutations_node[unknown]]

        node_flag = ts.nodes_flags[mutations_node]
        position = ts.sites_position[ts.mutations_site]

        tables = self.ts.tables
        assert np.all(
            tables.mutations.derived_state_offset == np.arange(ts.num_mutations + 1)
        )
        derived_state = tables.mutations.derived_state.view("S1").astype(str)

        assert np.all(
            tables.sites.ancestral_state_offset == np.arange(ts.num_sites + 1)
        )
        ancestral_state = tables.sites.ancestral_state.view("S1").astype(str)
        del tables
        inherited_state = ancestral_state[ts.mutations_site]
        mutations_with_parent = ts.mutations_parent != -1

        parent = ts.mutations_parent[mutations_with_parent]
        assert np.all(parent >= 0)
        inherited_state[mutations_with_parent] = derived_state[parent]
        self.mutations_derived_state = derived_state
        self.mutations_inherited_state = inherited_state

        self.mutations_position = ts.sites_position[ts.mutations_site].astype(int)
        N = ts.num_mutations
        mutations_num_descendants = np.zeros(N, dtype=int)
        mutations_num_inheritors = np.zeros(N, dtype=int)
        mutations_num_parents = np.zeros(N, dtype=int)

        tree = ts.first()

        for mut_id in np.arange(N):
            tree.seek(self.mutations_position[mut_id])
            mutation_node = ts.mutations_node[mut_id]
            descendants = tree.num_samples(mutation_node)
            mutations_num_descendants[mut_id] = descendants
            mutations_num_inheritors[mut_id] = descendants
            # Subtract this number of descendants from the parent mutation. We are
            # guaranteed to list parents mutations before their children
            parent = ts.mutations_parent[mut_id]
            if parent != -1:
                mutations_num_inheritors[parent] -= descendants

            num_parents = 0
            while parent != -1:
                num_parents += 1
                parent = ts.mutations_parent[parent]
            mutations_num_parents[mut_id] = num_parents

        df = pd.DataFrame(
            {
                "position": position,
                "node": ts.mutations_node,
                "time": mutations_time,
                "derived_state": self.mutations_derived_state,
                "inherited_state": self.mutations_inherited_state,
                "num_descendants": mutations_num_descendants,
                "num_inheritors": mutations_num_inheritors,
                "num_parents": mutations_num_parents,
            }
        )

        return df.astype(
            {
                "position": "float64",
                "node": "int",
                "time": "float64",
                "derived_state": "str",
                "inherited_state": "str",
                "num_descendants": "int",
                "num_inheritors": "int",
                "num_parents": "int",
            }
        )

    def edges_data(self):
        ts = self.ts
        left = ts.edges_left
        right = ts.edges_right
        edges_parent = ts.edges_parent
        edges_child = ts.edges_child
        nodes_time = ts.nodes_time
        parent_time = nodes_time[edges_parent]
        child_time = nodes_time[edges_child]
        branch_length = parent_time - child_time
        span = right - left

        df = pd.DataFrame(
            {
                "left": left,
                "right": right,
                "parent": edges_parent,
                "child": edges_child,
                "parent_time": parent_time,
                "child_time": child_time,
                "branch_length": branch_length,
                "span": span,
            }
        )

        return df.astype(
            {
                "left": "float64",
                "right": "float64",
                "parent": "int",
                "child": "int",
                "parent_time": "float64",
                "child_time": "float64",
                "branch_length": "float64",
                "span": "float64",
            }
        )

    def nodes_data(self):
        ts = self.ts
        child_left, child_right = self.child_bounds(
            ts.num_nodes, ts.edges_left, ts.edges_right, ts.edges_child
        )
        df = pd.DataFrame(
            {
                "time": ts.nodes_time,
                "num_mutations": self.nodes_num_mutations,
                "ancestors_span": child_right - child_left,
            }
        )
        return df.astype(
            {
                "time": "float64",
                "num_mutations": "int",
                "ancestors_span": "float64",
            }
        )

    def trees_data(self):
        ts = self.ts
        num_trees = ts.num_trees
        num_children_per_tree = np.zeros(num_trees)
        num_nodes_per_tree = np.zeros(num_trees)
        max_internal_arity = np.zeros(num_trees)

        total_branch_length, num_internal_nodes, max_arity = compute_per_tree_stats(ts)

        # FIXME - need to add this to the computation above
        mean_internal_arity = np.zeros(num_trees)

        site_tree_index = self.calc_site_tree_index()
        unique_values, counts = np.unique(site_tree_index, return_counts=True)
        sites_per_tree = np.zeros(ts.num_trees, dtype=np.int64)
        sites_per_tree[unique_values] = counts
        breakpoints = ts.breakpoints(as_array=True)
        df = pd.DataFrame(
            {
                "left": breakpoints[:-1],
                "right": breakpoints[1:],
                "total_branch_length": total_branch_length,
                "mean_internal_arity": mean_internal_arity,
                "max_internal_arity": max_arity,
                "num_sites": sites_per_tree,
            }
        )

        return df.astype(
            {
                "left": "int",
                "right": "int",
                "total_branch_length": "float64",
                "mean_internal_arity": "float64",
                "max_internal_arity": "float64",
                "num_sites": "int",
            }
        )

    def calc_polytomy_fractions(self):
        """
        Calculates the fraction of polytomies for each tree in the
        tree sequence
        """
        assert self.ts.num_samples > 2
        polytomy_fractions = []
        for tree in self.ts.trees():
            if tree.num_edges == 0:
                polytomy_fractions.append(None)
            else:
                polytomy_fractions.append(
                    float(
                        (tree.num_edges - self.ts.num_samples)
                        / (self.ts.num_samples - 2)
                    )
                )
        return polytomy_fractions

    def map_stats_to_genome(self, to_map):
        """
        Converts a list of tree-based stats to genomic coordinates
        """
        mapped = np.zeros(int(self.ts.sequence_length))
        for i, tree in enumerate(self.ts.trees()):
            left, right = map(int, tree.interval)
            mapped[left:right] = to_map[i]
        return mapped

    def make_sliding_windows(self, iterable, size, overlap=0):
        start = 0
        assert overlap < size, "overlap must be smaller then window size"
        end = size
        step = size - overlap

        length = len(iterable)
        while end < length:
            yield iterable[start:end]
            start += step
            end += step
        yield iterable[start:]

    def plot_polytomy_fractions(
        self, region_start=None, region_end=None, window_size=100_000, overlap=0
    ):
        """
        Plots the fraction of polytomies in windows actoss the genomic sequence
        """
        if region_start is None:
            region_start = max(0, self.ts.tables.sites.position[0] - 50_000)
        if region_end is None:
            region_end = self.ts.tables.sites.position[-1] + 50_000
        fig, ax = plt.subplots(figsize=(20, 5))
        polytomy_fractions = self.calc_polytomy_fractions()
        poly_fracs_by_pos = self.map_stats_to_genome(polytomy_fractions)
        poly_fracs_means = []
        poly_fracs_sd = []
        genomic_positions = []
        for poly_win in self.make_sliding_windows(
            poly_fracs_by_pos, window_size, overlap
        ):
            poly_fracs_means.append(np.mean(poly_win))
            poly_fracs_sd.append(np.std(poly_win))
        for gen_win in self.make_sliding_windows(
            np.arange(1, self.ts.sequence_length), window_size, overlap
        ):
            genomic_positions.append(gen_win[0] / 1_000_000)
        ax.plot(
            genomic_positions,
            poly_fracs_means,
            label="mean",
            linewidth=0.5,
        )
        ax.fill_between(
            genomic_positions,
            np.array(poly_fracs_means) - np.array(poly_fracs_sd),
            np.array(poly_fracs_means) + np.array(poly_fracs_sd),
            alpha=0.3,
            label="mean +/- std",
        )
        missing_vals = np.take(genomic_positions, np.where(np.isnan(poly_fracs_means)))
        ax.plot(
            missing_vals,
            np.zeros(len(missing_vals)),
            color="red",
            marker="o",
            label="missing data",
        )
        ax.set_xlabel(f"Position on chr {self.chr}(Mb)", fontsize=10)
        ax.set_ylabel("Window mean", fontsize=10)
        ax.set_title("Polytomy score", fontsize=10)
        ax.set_ylim(0, 1)
        ax.set_xlim(region_start / 1_000_000, region_end / 1_000_000)
        handles, labels = ax.get_legend_handles_labels()
        unique = [
            (h, l)
            for i, (h, l) in enumerate(zip(handles, labels))
            if l not in labels[:i]
        ]
        ax.legend(*zip(*unique))
        plt.show()

    def plot_mutations_per_site(self, max_num_muts=None, show_counts=False):
        fig, ax = plt.subplots()
        bins = None
        plt.xlabel("Number of mutations")
        if max_num_muts is not None:
            bins = range(max_num_muts + 1)
            sites_with_many_muts = np.sum(self.sites_num_mutations > max_num_muts)
            plt.xlabel(
                f"Number of mutations\n\n\nThere are {sites_with_many_muts:,} sites with more than {max_num_muts:,} mutations"
            )
        counts, edges, bars = plt.hist(
            self.sites_num_mutations, bins=bins, edgecolor="black"
        )
        ax.set_xticks(edges)
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, pos: "{:,}".format(int(x)))
        )
        plt.ylabel("Number of sites")
        plt.title("Mutations-per-site distribution")
        if show_counts:
            plt.bar_label(bars, fmt="{:,.0f}")

    def plot_mutations_per_site_along_seq(
        self, region_start=None, region_end=None, hist_bins=1000
    ):
        count = self.sites_num_mutations
        pos = self.ts.sites_position
        if region_start is None:
            region_start = pos[0]
        if region_end is None:
            region_end = pos[-1]
        grid = sns.jointplot(
            x=pos / 1_000_000,
            y=count,
            kind="scatter",
            marginal_ticks=True,
            alpha=0.5,
            marginal_kws=dict(bins=hist_bins),
            xlim=(region_start / 1_000_000, region_end / 1_000_000),
        )
        grid.ax_marg_y.remove()
        grid.fig.set_figwidth(20)
        grid.fig.set_figheight(8)
        grid.ax_joint.set_xlabel("Position on genome (Mb)")
        grid.ax_joint.set_ylabel("Number of mutations")

    def plot_mutations_per_node(self, max_num_muts=None, show_counts=False):
        fig, ax = plt.subplots()
        bins = None
        plt.xlabel(f"Number of mutations")
        if max_num_muts is not None:
            bins = range(max_num_muts + 1)
            nodes_with_many_muts = np.sum(self.nodes_num_mutations > max_num_muts)
            plt.xlabel(
                f"Number of mutations \n\n\nThere are {nodes_with_many_muts:,} nodes with more than {max_num_muts:,} mutations"
            )

        counts, edges, bars = plt.hist(
            self.nodes_num_mutations, bins=bins, edgecolor="black"
        )
        ax.set_xticks(edges)
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, pos: "{:,}".format(int(x)))
        )
        plt.ylabel("Number of nodes")
        plt.title("Mutations-per-node distribution")
        if show_counts:
            plt.bar_label(bars, fmt="{:,.0f}")

    def plot_tree_spans(
        self, log_transform=True, region_start=None, region_end=None, show_counts=False
    ):
        fig, ax = plt.subplots()
        bins = None
        breakpoints = self.ts.breakpoints(as_array=True)
        start_idx = 2
        end_idx = len(breakpoints) - 1

        if region_start is not None:
            start_idx = max(start_idx, np.argmax(breakpoints > region_start))
        if region_end is not None:
            end_idx = min(np.argmax(breakpoints >= region_end), end_idx)

        spans = (
            breakpoints[start_idx:end_idx] - breakpoints[start_idx - 1 : end_idx - 1]
        )
        xlabel = "span"
        if log_transform:
            spans = np.log10(spans)
            xlabel = "span (log10)"
            bins = range(int(np.min(spans)), int(np.max(spans)) + 2)

        counts, edges, bars = plt.hist(spans, edgecolor="black", bins=bins)
        ax.set_xticks(edges)
        if show_counts:
            plt.bar_label(bars, fmt="{:,.0f}")
        ax.set_xlabel(xlabel)
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, pos: "{:,}".format(int(x)))
        )
        plt.title(f"Distribution of {len(spans):,} tree spans")

    def calc_mean_node_arity(self):
        span_sums = np.bincount(
            self.ts.edges_parent,
            weights=self.ts.edges_right - self.ts.edges_left,
            minlength=self.ts.num_nodes,
        )
        node_spans = self.ts.sample_count_stat(
            [self.ts.samples()],
            lambda x: (x > 0),
            1,
            polarised=True,
            span_normalise=False,
            strict=False,
            mode="node",
        )[:, 0]
        return span_sums / node_spans

    def plot_mean_node_arity(self, show_counts=False):
        fig, ax = plt.subplots()
        mean_arity = self.calc_mean_node_arity()
        counts, edges, bars = plt.hist(mean_arity, bins=None, edgecolor="black")
        ax.set_xlabel("Mean node arity")
        ax.set_ylabel("Number of nodes")
        ax.set_title("Mean-node-arity distribution")
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, pos: "{:,}".format(int(x)))
        )
        if show_counts:
            plt.bar_label(bars, fmt="{:,.0f}")

    def calc_site_tree_index(self):
        return (
            np.searchsorted(
                self.ts.breakpoints(as_array=True), self.ts.sites_position, side="right"
            )
            - 1
        )

    def calc_sites_per_tree(self):
        site_tree_index = self.calc_site_tree_index()
        unique_values, counts = np.unique(site_tree_index, return_counts=True)
        sites_per_tree = np.zeros(self.ts.num_trees, dtype=np.int64)
        sites_per_tree[unique_values] = counts
        return sites_per_tree

    def calc_mutations_per_tree(self):
        site_tree_index = self.calc_site_tree_index()
        mutation_tree_index = site_tree_index[self.ts.mutations_site]
        unique_values, counts = np.unique(mutation_tree_index, return_counts=True)
        mutations_per_tree = np.zeros(self.ts.num_trees, dtype=np.int64)
        mutations_per_tree[unique_values] = counts
        return mutations_per_tree

    def plot_mutations_per_tree(self, max_num_muts=None, show_counts=False):
        fig, ax = plt.subplots()
        tree_mutations = self.calc_mutations_per_tree()
        bins = max(100, int(np.sqrt(self.ts.num_trees)))
        plt.xlabel(f"Number of mutations")
        if max_num_muts is not None:
            bins = range(max_num_muts + 1)
            trees_with_many_muts = np.sum(tree_mutations > max_num_muts)
            plt.xlabel(
                f"Number of mutations\n\n\nThere are {trees_with_many_muts:,} trees with more than {max_num_muts:,} mutations"
            )

        counts, edges, bars = plt.hist(
            self.calc_mutations_per_tree(), bins=bins, edgecolor="black"
        )
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, pos: "{:,}".format(int(x)))
        )
        plt.ylabel("Number of trees")
        plt.title("Mutations-per-tree distribution")
        if show_counts:
            plt.bar_label(bars, fmt="{:,.0f}")

    def plot_mutations_per_tree_along_seq(
        self, region_start=None, region_end=None, hist_bins=1000
    ):
        tree_mutations = self.calc_mutations_per_tree()
        tree_mutations = tree_mutations[1:-1]
        breakpoints = self.ts.breakpoints(as_array=True)
        tree_mids = breakpoints[1:] - ((breakpoints[1:] - breakpoints[:-1]) / 2)
        tree_mids = tree_mids[1:-1]
        if region_start is None or region_start < tree_mids[0]:
            region_start = tree_mids[0]
        if region_end is None or region_end > tree_mids[-1]:
            region_end = tree_mids[-1]

        grid = sns.jointplot(
            x=tree_mids / 1_000_000,
            y=tree_mutations,
            kind="scatter",
            marginal_ticks=True,
            alpha=0.5,
            marginal_kws=dict(bins=hist_bins),
            xlim=(region_start / 1_000_000, region_end / 1_000_000),
            # set ylim to the max number of sites in a tree in the region
            ylim=(
                0,
                np.max(
                    tree_mutations[
                        (tree_mids >= region_start) & (tree_mids <= region_end)
                    ]
                ),
            ),
        )
        grid.ax_marg_y.remove()
        grid.fig.set_figwidth(20)
        grid.fig.set_figheight(8)
        grid.ax_joint.set_xlabel("Position on genome (Mb)")
        grid.ax_joint.set_ylabel("Number of mutations per tree")

    def plot_sites_per_tree(self, max_num_sites=None, show_counts=False):
        fig, ax = plt.subplots()
        bins = max(100, int(np.sqrt(self.ts.num_trees)))
        plt.xlabel(f"Number of sites")
        if max_num_sites is not None:
            bins = range(max_num_sites + 1)
            trees_with_many_sites = np.sum(self.calc_sites_per_tree() > max_num_sites)
            plt.xlabel(
                f"Number of sites\n\n\nThere are {trees_with_many_sites:,} trees with more than {max_num_sites:,} sites"
            )

        counts, edges, bars = plt.hist(
            self.calc_sites_per_tree(), bins=bins, edgecolor="black"
        )
        ax.yaxis.set_major_formatter(
            plt.FuncFormatter(lambda x, pos: "{:,}".format(int(x)))
        )

        plt.ylabel("Number of trees")
        plt.title("Sites-per-tree distribution")
        if show_counts:
            plt.bar_label(bars, fmt="{:,.0f}")

    def plot_sites_per_tree_along_seq(
        self, region_start=None, region_end=None, hist_bins=500
    ):
        tree_sites = self.calc_sites_per_tree()
        tree_sites = tree_sites[1:-1]
        breakpoints = self.ts.breakpoints(as_array=True)
        tree_mids = breakpoints[1:] - ((breakpoints[1:] - breakpoints[:-1]) / 2)
        tree_mids = tree_mids[1:-1]
        if region_start is None or region_start < tree_mids[0]:
            region_start = tree_mids[0]
        if region_end is None or region_end > tree_mids[-1]:
            region_end = tree_mids[-1]

        grid = sns.jointplot(
            x=tree_mids / 1_000_000,
            y=tree_sites,
            kind="scatter",
            marginal_ticks=True,
            alpha=0.5,
            marginal_kws=dict(bins=hist_bins),
            xlim=(region_start / 1_000_000, region_end / 1_000_000),
            ylim=(
                0,
                np.max(
                    tree_sites[(tree_mids >= region_start) & (tree_mids <= region_end)]
                ),
            ),
        )
        grid.ax_marg_y.remove()
        grid.fig.set_figwidth(20)
        grid.fig.set_figheight(8)
        grid.ax_joint.set_xlabel("Position on genome (Mb)")
        grid.ax_joint.set_ylabel("Number of sites per tree")
