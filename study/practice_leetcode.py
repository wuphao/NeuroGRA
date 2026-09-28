class TreeNode(object):
    def __init__(self, val=0, left=None, right=None):
        self.val = val
        self.left = left
        self.right = right
class Node:
    def __init__(self, x, next=None, random=None):
        self.val = int(x)
        self.next = next
        self.random = random
class Solution:

    def partition(self, s):
        """
        :type s: str
        :rtype: List[List[str]]
        """
        n = len(s)
        ans = []
        
        f = [[True] * n for _ in range(n)]
        for i in range(n - 1, -1, -1):
            for j in range(i + 1, n):
                f[i][j] = (s[i] == s[j]) and f[i + 1][j - 1]
        
        splits = []  #存多个字串的列表
        
        def dfs(i):
            if i == n:
                ans.append(splits[:])   
                return
            for j in range(i, n):
                if f[i][j]:
                    splits.append(s[i:j + 1])
                    dfs(j + 1)  #找下一个回文串了（分割）
                    splits.pop()      
        
        dfs(0)
        return ans
    
    def kthSmallest(self, root, k):
        """
        :type root: Optional[TreeNode]
        :type k: int
        :rtype: int
        """
    

        def findk(root,k):
            if not root:
                return 
            findk(root.left,k)
            self.i+=1
            if self.i==k:
              self.res = root.val
            findk(root.right,k)

        findk(root,k)
        return self.res
    
    def bfs(self, root, depth, res):
        if not root:
            return
        if len(res) == depth:
            res.append([])
        self.bfs(root.left, depth + 1, res)
        res[depth].append(root.val)
        self.bfs(root.right, depth + 1, res)

    def levelOrder(self, root):
        res = []
        if not root:
            return res
        self.bfs(root, 0, res)
        return res

    def rightSideView(self, root):
        """
        :type root: Optional[TreeNode]
        :rtype: List[int]
        """
        res = []
        order = self.levelOrder(root)
        for o in order:
            res.append(o[len(o)-1])

        return res

    def __init__(self):
        self.prev = None

    def flatten(self, root):
        """
        :type root: Optional[TreeNode]
        :rtype: None Do not return anything, modify root in-place instead.
        """
        def dfs(node):
            if not node:
                return
            dfs(node.right)    
            dfs(node.left)       
            node.left = None    
            node.right = self.prev   
            self.prev  = node         
        dfs(root)

    def coinChange(self, coins, amount):
        """
        :type coins: List[int]
        :type amount: int
        :rtype: int
        """
        dp = [0] *(amount+1)
        for i in range(1,amount+1):
            minnum = 100000000
            for coin in coins:
                if i>=coin and dp[i-coin]!= -1:
                    minnum = min(minnum,dp[i-coin]+1)
            if minnum ==100000000:
                dp[i] = -1
            else:
                dp[i] = minnum
        return dp[amount]
    def lengthOfLIS(self, nums):
        """
        :type nums: List[int]
        :rtype: int
        """
        n = len(nums)
        dp=[1]*n
        for i in range(1,n):
            maxlength = 1
            for j in range(0,i):
                if nums[j]<nums[i]:
                    maxlength = max(maxlength,dp[j]+1)
            dp[i] = maxlength
        return max(dp)
    
    def maxProduct(self, nums):
        """
        :type nums: List[int]
        :rtype: int
        """
        dp = nums[:]
        dp1 = nums[:]
        for i in range(i,len(nums)):
            dp[i] = max(max(dp[i-1]*nums[i],dp[i]),dp1[i-1]*nums[i])
            dp1[i] = min(min(dp1[i-1]*nums[i],dp1[i]),dp[i-1]*nums[i])
        return max(dp)
    def canPartition(self, nums):
        total = sum(nums)
        if total % 2:
            return False

        target = total // 2
        dp = [False] * (target + 1)
        dp[0] = True

        for num in nums:
            for j in range(target, num - 1, -1): #让变量 j 从 target 开始，每次减 1，一直到 num（包含 num）为止  0/1背包问题
                dp[j] = dp[j] or dp[j - num]

        return dp[target]
    def wordBreak(self, s, wordDict):
        wordDictSet = set(wordDict)

        dp = [False] * (len(s) + 1)
        dp[0] = True

        for i in range(1, len(s) + 1):
            for j in range(i):
                if dp[j] and s[j:i] in wordDictSet:
                    dp[i] = True
                    break

        return dp[len(s)]
    def longestValidParentheses(self, s):
        """
        :type s: str
        :rtype: int
        """
        maxans = 0
        stack = [-1]

        for i, ch in enumerate(s):
            if ch == '(':
                stack.append(i)
            else:
                stack.pop()
                if not stack:
                    stack.append(i)
                else:
                    maxans = max(maxans, i - stack[-1])

        return maxans
    def partitionLabels(self, s):
        """
        :type s: str
        :rtype: List[int]
        """
        res =[]
        minindex = {}
        maxindex = {}
        for index, c in enumerate(s):
            if c not in minindex:
                minindex[c] = index
                maxindex[c] = index
            else:
                if index >maxindex[c]:
                    maxindex[c]=index
                if index<minindex[c]:
                    minindex[c]=index
        templeft = -1
        tempright = -1
        for c in sorted(minindex, key=lambda x: minindex[x]):
            if tempright<minindex[c]:
                if tempright !=-1:
                    res.append(tempright-templeft+1)
                templeft = minindex[c]
                tempright= maxindex[c]
            elif minindex[c]>templeft and maxindex[c]>tempright:
                tempright = maxindex[c]
            elif minindex[c]<templeft and maxindex[c]<tempright:
                templeft = minindex[c]
            elif minindex[c]<templeft and maxindex[c]>tempright:
                templeft = minindex[c]
                tempright = maxindex[c]
            else:
                continue
        res.append(tempright-templeft+1)
        return res
    def reverse(self,head,k):
        pre = None
        curr = head
        if not head or not head.next:
            return head
        nex = head.next
        for i in range(0,k):
            if not curr:
                return
            curr.next = pre
            pre = curr
            curr = nex
            nex = nex.next

    def copyRandomList(self, head):
        """
        :type head: Node
        :rtype: Node
        """
        newhead = None
        h = head
        nodemap={}
        while h:
            curr = Node(h.val)
            nodemap[h]=curr
            if h==head:
                newhead = curr
            h=h.next
        h1 = head
        nh1 = newhead
        while h1:
            if not h1.random:
                nh1.random = None
            else:
                nh1.random = nodemap[h1.random]
            if not h1.next:
                nh1.next  = None
            else:
                nh1.next = nodemap[h1.next]
            nh1 = nh1.next
            h1=h1.next
        return newhead
    def searchMatrix(self, matrix, target):
        """
        :type matrix: List[List[int]]
        :type target: int
        :rtype: bool
        """
        m =len(matrix)
        n = len(matrix[0])
        rowleft = 0
        rowright = m-1
        while rowleft<=rowright:
            mid = (rowright+rowleft) //2
            if matrix[mid][0] == target:
                return True
            if matrix[mid][0] >target:
                rowright =mid -1
            if matrix[mid][0]<target:
                rowleft = mid +1
        row = rowleft -1
        cleft = 0
        cright = n-1
        while cleft<=cright:
            mid = (cleft+cright) //2
            if matrix[row][mid] ==target:
                return True
            if matrix[row][mid]>target:
                cright = mid -1
            if matrix[row][mid]<target:
                cleft = mid +1
        return False
    def findMin(self, nums):
        """
        :type nums: List[int]
        :rtype: int
        """
        minnum = 1000000
        n = len(nums)
        if nums[n-1]>nums[0]:
            return nums[0]
        l = 0
        r= n-1
        while l<=r:
            m = (l+r)/2
            if nums[m]<minnum:
                minnum = nums[m]
            if nums[m]>=nums[l] :
                l = m+1
            else :
                r= m-1
        return minnum


class LRUCache(object):
    def __init__(self, capacity):
        """
        :type capacity: int
        """
        

    def get(self, key):
        """
        :type key: int
        :rtype: int
        """
        

    def put(self, key, value):
        """
        :type key: int
        :type value: int
        :rtype: None
        """
        
        
            
    

        


        


            

        
            




        

        

        
        


        
        
        



        
def main():
    solution = Solution()
    print(solution.groupAnagrams(["eat", "tea", "tan", "ate", "nat", "bat"]))
    print(solution.maxArea([1,8,6,2,5,4,8,3,7]))
    print(solution.threeSum([-1,0,1,2,-1,-4]))
    print(solution.trap([0,1,0,2,1,0,1,3,2,1,2,1]))


if __name__ == "__main__":
    main()